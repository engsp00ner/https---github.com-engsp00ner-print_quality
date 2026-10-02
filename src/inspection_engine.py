from dataclasses import asdict
import logging
from pathlib import Path
import time
import numpy as np
from config import Settings
from .alignment import align, prepare_features
from .blob_detection import detect_blobs
from .defect_merging import merge_defects
from .image_loader import load_image, save_image
from .ink_detection import compare_ink
from .line_detection import detect_lines
from .models import InspectionResult
from .ocr_engine import OCREngine
from .preprocessing import for_defects, ink_mask
from .reporting import create_batch_dir, create_image_dir, write_json
from .ssim_comparison import compare_ssim
from .text_comparison import compare_text
from .visualization import annotate

log = logging.getLogger(__name__)


class InspectionEngine:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.ocr_engine = OCREngine(self.settings)
        self.reference_path = None
        self.reference_signature = None
        self.reference_preparation_seconds = 0.0

    def prepare_reference(self, path):
        started = time.perf_counter()
        path = Path(path).resolve()
        stat = path.stat()
        signature = (str(path), stat.st_mtime_ns, stat.st_size)
        if signature == self.reference_signature:
            return
        self.reference = load_image(path, self.settings)
        self.reference_gray = for_defects(self.reference)
        self.reference_ink = ink_mask(self.reference_gray, self.settings)
        self.reference_features = prepare_features(self.reference_gray, self.settings)
        self.reference_ocr = self.ocr_engine.extract(self.reference)
        self.reference_path = path
        self.reference_signature = signature
        self.reference_preparation_seconds = time.perf_counter() - started
        log.info("Prepared reference %s (OCR: %s)", path.name, self.reference_ocr.status)

    def inspect(self, reference_path, printed_path, batch_dir=None):
        started = time.perf_counter()
        printed_path = Path(printed_path).resolve()
        result = InspectionResult(printed_path.name, str(printed_path), str(Path(reference_path).resolve()))
        output = None
        try:
            self.prepare_reference(reference_path)
            output = create_image_dir(batch_dir or create_batch_dir(self.settings.output_dir), printed_path)
            result.output_dir = str(output.resolve())
            printed = load_image(printed_path, self.settings)
            aligned, valid, result.alignment = align(self.reference_gray, printed, self.settings, self.reference_features)
            result.warnings.extend(result.alignment["warnings"])
            registered = result.alignment["status"] != "FAILED"
            arrays = {"original_printed": printed, "aligned_printed": aligned}
            defects, streaks, blobs = [], [], []
            if registered:
                gray = for_defects(aligned)
                ssim, difference, visual = compare_ssim(self.reference_gray, gray, valid, self.settings)
                extra, missing, ink_metrics, ink_defects = compare_ink(self.reference_gray, gray, valid, self.settings, self.reference_ink)
                streaks = detect_lines(extra, missing, self.settings)
                blobs = detect_blobs(extra, missing, self.settings)
                result.metrics.update(ssim=ssim, **ink_metrics)
                defects = ink_defects + streaks + blobs + visual
            else:
                difference = extra = missing = np.zeros(self.reference_gray.shape, np.uint8)
                result.metrics["ssim"] = None
                result.decision_reasons.append("Alignment failed; inspection incomplete")
            arrays.update(difference_mask=difference, extra_ink_mask=extra, missing_ink_mask=missing,
                          detected_lines=annotate(aligned, streaks), valid_comparison_mask=valid)
            printed_ocr = self.ocr_engine.extract(aligned)
            result.ocr = {"reference": asdict(self.reference_ocr), "printed": asdict(printed_ocr)}
            result.metrics.update(ocr_reference_confidence=self.reference_ocr.confidence,
                                  ocr_printed_confidence=printed_ocr.confidence,
                                  reference_preparation_seconds=self.reference_preparation_seconds)
            ocr_ok = self.reference_ocr.status == printed_ocr.status == "SUCCESS"
            text_defects = []
            if ocr_ok:
                text_metrics, text_defects = compare_text(self.reference_ocr, printed_ocr, self.settings)
                result.ocr["comparison"] = text_metrics
                result.metrics.update({k: v for k, v in text_metrics.items() if not isinstance(v, list)})
                if registered:
                    defects.extend(text_defects)
                for name, ocr in (("Reference", self.reference_ocr), ("Printed", printed_ocr)):
                    if ocr.confidence is not None and ocr.confidence < self.settings.ocr_min_confidence:
                        result.warnings.append(f"{name} OCR confidence is low; verify the content manually.")
                        result.decision_reasons.append("Low OCR confidence; manual content review required")
            else:
                result.warnings.extend(dict.fromkeys(o.error for o in (self.reference_ocr, printed_ocr) if o.error))
                result.decision_reasons.append("OCR unavailable or failed; content inspection incomplete")
            coverage_ok = result.alignment.get("coverage", 0) >= self.settings.min_coverage
            if registered and not coverage_ok:
                result.decision_reasons.append("Page coverage is insufficient")
            result.inspection_complete = bool(registered and coverage_ok and ocr_ok)
            result.defects = merge_defects(defects, self.settings)
            result.metrics.update(text_error_count=len(text_defects), streak_count=len(streaks), blob_count=len(blobs),
                                  extra_ink_count=sum(d.type == "EXTRA_INK" for d in defects),
                                  missing_ink_count=sum(d.type == "MISSING_INK" for d in defects),
                                  total_defect_count=len(result.defects))
            if result.defects:
                result.decision_reasons.append("Localized defects detected")
            for metric, threshold, lower in (("ssim", self.settings.ssim_threshold, True),
                                             ("text_similarity", self.settings.text_similarity_threshold, True),
                                             ("extra_ink_ratio", self.settings.extra_ink_threshold, False),
                                             ("missing_ink_ratio", self.settings.missing_ink_threshold, False)):
                value = result.metrics.get(metric)
                if value is not None and (value < threshold if lower else value > threshold):
                    result.decision_reasons.append(f"{metric}={value:.6f} outside threshold {threshold}")
            result.status = "DEFECTIVE" if result.decision_reasons else "PASS"
            arrays["defect_overlay"] = annotate(aligned, result.defects)
            for name, array in arrays.items():
                target = output / f"{name}.png"
                save_image(target, array)
                result.artifacts[name] = str(target.resolve())
        except Exception as exc:
            log.exception("Inspection failed for %s", printed_path)
            result.status = "DEFECTIVE"
            result.inspection_complete = False
            result.warnings.append(f"{type(exc).__name__}: {exc}")
            result.decision_reasons.append("Processing error; manual review required")
        result.processing_time_seconds = time.perf_counter() - started
        if output is not None:
            try:
                write_json(output / "report.json", {**result.to_dict(), "settings": self.settings.to_dict(), "schema_version": 1})
            except OSError as exc:
                log.exception("Could not save image report")
                result.status = "DEFECTIVE"
                result.inspection_complete = False
                result.warnings.append(f"Report write failed: {exc}")
                result.decision_reasons.append("Report write failure")
        log.info("%s: %s, %d regions, %.2fs", result.filename, result.status, len(result.defects), result.processing_time_seconds)
        return result
