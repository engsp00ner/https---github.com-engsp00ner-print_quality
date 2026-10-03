from dataclasses import asdict
from copy import deepcopy
import logging
import hashlib
from pathlib import Path
import time
from collections import Counter
import cv2
import numpy as np
from config import Settings
from .alignment import align, prepare_features, geometric_orientation, full_page_frame
from .blob_detection import detect_blobs
from .defect_merging import merge_defects
from .image_loader import load_image, save_image
from .ink_detection import compare_ink
from .line_detection import detect_lines
from .models import InspectionResult, OCRResult
from .ocr_engine import OCREngine, OrientationOCR
from .paddle_runtime import fingerprint
from .orientation import OrientationDetector, RESOLVED, ANGLES, normalize, rotate_page
from .preprocessing import for_defects, ink_mask
from .reporting import create_batch_dir, create_image_dir, write_json
from .ssim_comparison import compare_ssim
from .text_comparison import compare_text
from .visualization import annotate

log = logging.getLogger(__name__)


class InspectionEngine:
    def __init__(self, settings=None, reference_orientation=None, printed_orientations=None, progress_callback=None):
        self.progress_callback = progress_callback
        self.settings = settings or Settings()
        self.ocr_engine = OCREngine(self.settings)
        self.orientation_detector = OrientationDetector(self.settings, OrientationOCR(self.settings))
        self.reference_orientation_override = reference_orientation
        self.printed_orientation_overrides = {str(Path(p).resolve()): angle
                                               for p, angle in (printed_orientations or {}).items()}
        self.reference_path = None
        self.reference_signature = None
        self.reference_preparation_seconds = 0.0

    def _notify(self, name, status, **data):
        if self.progress_callback:
            self.progress_callback({"stage": name, "status": status, **data})

    def _stage(self, result, name, action=None, *, reason="", required=True):
        """Run one independent check and retain its own outcome.

        A failed detector must not abort unrelated detectors or discard their
        evidence.  The action returns either a value or ``(value, count)``.
        """
        if action is None:
            result.checks[name] = {"status": "SKIPPED", "reason": reason,
                                   "required": required, "elapsed_seconds": 0.0, "result_count": 0}
            log.info("Check %s skipped: %s", name, reason)
            self._notify(name, "SKIPPED", reason=reason)
            return None
        started = time.perf_counter()
        log.info("Check %s started", name)
        self._notify(name, "RUNNING")
        try:
            value = action()
            payload, count = value if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int) else (value, 0)
            result.checks[name] = {"status": "COMPLETED", "required": required,
                                   "elapsed_seconds": time.perf_counter() - started, "result_count": count}
            log.info("Check %s completed in %.3fs (%d results)", name,
                     result.checks[name]["elapsed_seconds"], count)
            # Completion of a call alone does not mean the check succeeded.
            display_status = "COMPLETED"
            if name == "printed_ocr" and getattr(payload, "status", "") != "SUCCESS":
                display_status = "FAILED" if getattr(payload, "status", "") == "FAILED" else "SKIPPED"
            if name == "alignment" and payload[2].get("status") == "FAILED":
                display_status = "FAILED"
            self._notify(name, display_status)
            return payload
        except Exception as exc:
            result.checks[name] = {"status": "FAILED", "required": required,
                                   "elapsed_seconds": time.perf_counter() - started,
                                   "result_count": 0, "reason": f"{type(exc).__name__}: {exc}"}
            result.warnings.append(f"{name} check failed: {type(exc).__name__}: {exc}")
            result.decision_reasons.append(f"{name} check failed; manual review required")
            log.exception("Check %s failed after %.3fs", name, result.checks[name]["elapsed_seconds"])
            self._notify(name, "FAILED", reason=result.checks[name]["reason"])
            return None

    def _skip_visual_checks(self, result, reason):
        for name in ("ssim", "ink", "streak", "blob"):
            self._stage(result, name, reason=reason)

    def prepare_reference(self, path):
        started = time.perf_counter()
        path = Path(path).resolve()
        stat = path.stat()
        signature = (hashlib.sha256(path.read_bytes()).hexdigest(), self.settings,
                     self.reference_orientation_override, fingerprint(self.settings) if self.settings.ocr_enabled else None)
        if signature == self.reference_signature:
            self.reference_path = path
            return
        self._notify("Reference preparation", "RUNNING")
        if self.orientation_detector.settings != self.settings:
            self.ocr_engine = OCREngine(self.settings)
            self.orientation_detector = OrientationDetector(self.settings, OrientationOCR(self.settings))
        self.reference_original = load_image(path, self.settings)
        if not self.settings.ocr_enabled and self.reference_orientation_override is None:
            self.reference, self.reference_orientation = normalize(self.reference_original, dict(
                status="MANUAL", method="reference_frame", correction_clockwise=0,
                warnings=[], elapsed_seconds=0.0))
        else:
            self.reference, self.reference_orientation = self.orientation_detector.detect(
                self.reference_original, self.reference_orientation_override)
        self.reference_gray = for_defects(self.reference)
        self.reference_ink = ink_mask(self.reference_gray, self.settings)
        self.reference_features = prepare_features(self.reference_gray, self.settings)
        self.reference_ocr = (self.ocr_engine.extract(self.reference) if self.settings.ocr_enabled
                              else OCRResult(status="SKIPPED", error="OCR disabled; visual inspection only."))
        self.reference_path = path
        self.reference_signature = signature
        self.reference_preparation_seconds = time.perf_counter() - started
        self._notify("Reference preparation", "COMPLETED")
        log.info("Prepared reference %s (OCR: %s)", path.name, self.reference_ocr.status)

    def _printed_orientation(self, printed, path):
        override = self.printed_orientation_overrides.get(str(path))
        if override is None and self.settings.auto_orientation and self.reference_orientation["status"] in RESOLVED:
            started = time.monotonic()
            geometry = geometric_orientation(self.reference_gray, printed, self.settings, self.reference_features)
            if geometry is not None:
                geometry["elapsed_seconds"] = time.monotonic() - started
                return normalize(printed, geometry)
            if printed.shape[:2] == self.reference_gray.shape and np.array_equal(for_defects(printed), self.reference_gray):
                return normalize(printed, dict(status="CONFIDENT", method="identical_reference",
                    correction_clockwise=0, warnings=[], elapsed_seconds=time.monotonic() - started))
        if override is None and not self.settings.auto_orientation and not self.settings.ocr_enabled:
            override = 0  # Explicitly use the orientation supplied in the UI.
        normalized, info = self.orientation_detector.detect(
            printed, override)
        # Registration is supplementary evidence only. Never choose by OCR edits
        # or number of defects. Strong correlation and a clear margin are required.
        if (info["status"] in RESOLVED or self.reference_orientation["status"] not in RESOLVED
                or not self.settings.auto_orientation or not self.settings.ocr_enabled):
            return normalized, info
        started = time.monotonic()
        candidates = []
        for angle in ANGLES:
            # Bound fallback as a whole; partial candidate sets cannot win.
            if time.monotonic() - started + info["elapsed_seconds"] >= self.settings.orientation_timeout:
                break
            candidate = rotate_page(printed, angle)
            aligned, valid, registration = align(self.reference_gray, candidate, self.settings, self.reference_features)
            quality = 0.0
            if registration["status"] != "FAILED" and registration["coverage"] >= self.settings.min_coverage:
                mask = valid[::4, ::4] > 0
                a = self.reference_gray[::4, ::4][mask].astype(float)
                b = for_defects(aligned)[::4, ::4][mask].astype(float)
                if a.size and a.std() > 0 and b.std() > 0:
                    quality = float(np.corrcoef(a, b)[0, 1])
            candidates.append({"angle": angle, "correlation": quality,
                               "status": registration["status"], "coverage": registration["coverage"]})
        info["registration_candidates"] = candidates
        if len(candidates) == 4:
            ranked = sorted(candidates, key=lambda c: c["correlation"], reverse=True)
            margin = ranked[0]["correlation"] - ranked[1]["correlation"]
            if ranked[0]["correlation"] >= max(0.85, self.settings.min_alignment_correlation) and margin >= 0.10:
                info.update(status="CONFIDENT", method="reference_registration",
                            correction_clockwise=ranked[0]["angle"], registration_margin=margin)
                info["warnings"] = [w for w in info["warnings"] if not w.startswith("Text orientation unresolved;")]
        info["elapsed_seconds"] += time.monotonic() - started
        return normalize(printed, info)

    def inspect(self, reference_path, printed_path, batch_dir=None):
        started = time.perf_counter()
        printed_path = Path(printed_path).resolve()
        result = InspectionResult(printed_path.name, str(printed_path), str(Path(reference_path).resolve()))
        output = None
        saving_started = False
        saving_failed = False
        try:
            self.prepare_reference(reference_path)
            output = create_image_dir(batch_dir or create_batch_dir(self.settings.output_dir), printed_path)
            result.output_dir = str(output.resolve())
            printed = load_image(printed_path, self.settings)
            self._notify("orientation", "RUNNING")
            normalized, printed_orientation = self._printed_orientation(printed, printed_path)
            orientation_ok = (self.reference_orientation["status"] in RESOLVED
                              and printed_orientation["status"] in RESOLVED)
            result.orientation = {"reference": self.reference_orientation, "printed": printed_orientation,
                                  "comparison_frame": "normalized_reference"}
            for name, info in (("Reference", self.reference_orientation), ("Printed", printed_orientation)):
                result.warnings.extend(f"{name}: {w}" for w in info["warnings"])
                if info["status"] not in RESOLVED:
                    result.decision_reasons.append(f"{name} orientation unresolved; manual correction required")
            result.checks["orientation"] = {"status": "COMPLETED" if orientation_ok else "SKIPPED",
                                            "required": True, "result_count": 0,
                                            "reason": "" if orientation_ok else "Direction must be resolved before reliable pixel comparison."}
            self._notify("orientation", result.checks["orientation"]["status"])
            alignment_stage = self._stage(result, "alignment", lambda: (align(
                self.reference_gray, normalized, self.settings, self.reference_features), 0))
            if alignment_stage is None:
                aligned = cv2.resize(normalized, (self.reference_gray.shape[1], self.reference_gray.shape[0]))
                valid = np.zeros(self.reference_gray.shape, np.uint8)
                result.alignment = {"status": "FAILED", "warnings": ["Alignment check failed."], "coverage": 0.0}
            else:
                aligned, valid, result.alignment = alignment_stage
            comparison_reference, aligned, valid, offset = full_page_frame(
                self.reference, normalized, aligned, valid, result.alignment)
            reference_gray = for_defects(comparison_reference)
            reference_ink = (self.reference_ink if comparison_reference.shape == self.reference.shape
                             else ink_mask(reference_gray, self.settings))
            result.orientation["comparison_frame"] = "full_page"
            result.orientation["reference_offset"] = list(offset)
            if "homography" in result.alignment:
                mapping = np.asarray(result.alignment["homography"]) @ np.asarray(printed_orientation["forward_transform"])
                result.alignment.update(loaded_printed_to_reference=mapping.tolist(),
                                        reference_to_loaded_printed=np.linalg.inv(mapping).tolist(),
                                        homography_source_frame="normalized_printed",
                                        destination_frame="full_page")
            result.warnings.extend(result.alignment["warnings"])
            registered = result.alignment["status"] != "FAILED"
            coverage_ok = result.alignment.get("coverage", 0) >= self.settings.min_coverage
            arrays = {"original_printed": printed, "normalized_printed": normalized,
                      "normalized_reference": comparison_reference, "aligned_printed": aligned}
            defects, streaks, blobs = [], [], []
            difference = extra = missing = np.zeros(reference_gray.shape, np.uint8)
            if registered and orientation_ok:
                gray = for_defects(aligned)
                def run_ssim():
                    response = compare_ssim(reference_gray, gray, valid, self.settings)
                    return response, len(response[2])
                ssim_stage = self._stage(result, "ssim", run_ssim)
                if ssim_stage is not None:
                    ssim, difference, visual = ssim_stage
                    result.metrics["ssim"] = ssim
                    defects.extend(visual)
                def run_ink():
                    response = compare_ink(reference_gray, gray, valid, self.settings, reference_ink)
                    return response, len(response[3])
                ink_stage = self._stage(result, "ink", run_ink)
                if ink_stage is not None:
                    extra, missing, ink_metrics, ink_defects = ink_stage
                    result.metrics.update(ink_metrics)
                    defects.extend(ink_defects)
                    def run_streak():
                        response = detect_lines(extra, missing, self.settings)
                        return response, len(response)
                    streak_stage = self._stage(result, "streak", run_streak)
                    if streak_stage is not None:
                        streaks = streak_stage
                        defects.extend(streaks)
                    def run_blob():
                        response = detect_blobs(extra, missing, self.settings)
                        return response, len(response)
                    blob_stage = self._stage(result, "blob", run_blob)
                    if blob_stage is not None:
                        blobs = blob_stage
                        defects.extend(blobs)
                else:
                    self._stage(result, "streak", reason="Ink check failed; streak detection has no masks.")
                    self._stage(result, "blob", reason="Ink check failed; blob detection has no masks.")
            else:
                reason = ("Alignment failed; pixel checks skipped."
                          if not registered else "Text direction unresolved; pixel checks skipped."
                          if not orientation_ok else "Page coverage is insufficient; pixel checks skipped.")
                self._skip_visual_checks(result, reason)
                result.metrics["ssim"] = None
                result.decision_reasons.append(reason)
            arrays.update(difference_mask=difference, extra_ink_mask=extra, missing_ink_mask=missing,
                          detected_lines=annotate(aligned, streaks), valid_comparison_mask=valid)
            reference_ocr_status = ("SKIPPED" if not self.settings.ocr_enabled else
                                    "COMPLETED" if self.reference_ocr.status == "SUCCESS" else "FAILED")
            result.checks["reference_ocr"] = {"status": reference_ocr_status, "required": self.settings.ocr_enabled,
                                               "result_count": len(self.reference_ocr.words),
                                               "elapsed_seconds": self.reference_preparation_seconds,
                                               **({"reason": self.reference_ocr.error} if self.reference_ocr.error else {})}
            log.info("Check reference_ocr %s (%d words)", reference_ocr_status.lower(), len(self.reference_ocr.words))
            printed_ocr = self._stage(result, "printed_ocr", (lambda: (
                self.ocr_engine.extract(aligned), 0)) if self.settings.ocr_enabled else None,
                reason="OCR disabled; visual inspection only.", required=self.settings.ocr_enabled)
            if printed_ocr is None:
                printed_ocr = OCRResult(status="FAILED" if self.settings.ocr_enabled else "SKIPPED",
                                       error=result.checks["printed_ocr"].get("reason", "OCR check failed"))
            reference_ocr = deepcopy(self.reference_ocr)
            for word in reference_ocr.words:
                x, y, w, h = word.bbox
                word.bbox = (x + offset[0], y + offset[1], w, h)
                word.polygon = [[x + offset[0], y + offset[1]] for x, y in word.polygon]
            result.ocr = {"reference": asdict(reference_ocr), "printed": asdict(printed_ocr)}
            result.metrics["inspection_scope"] = "visual_and_ocr" if self.settings.ocr_enabled else "visual_only"
            result.metrics.update(ocr_reference_confidence=self.reference_ocr.confidence,
                                  ocr_printed_confidence=printed_ocr.confidence,
                                  reference_preparation_seconds=self.reference_preparation_seconds)
            ocr_ok = self.reference_ocr.status == printed_ocr.status == "SUCCESS"
            text_defects = []
            if ocr_ok:
                def run_text_comparison():
                    response = compare_text(reference_ocr, printed_ocr, self.settings)
                    return response, len(response[1])
                text_stage = self._stage(result, "text_comparison", run_text_comparison)
                if text_stage is not None:
                    text_metrics, text_defects = text_stage
                    result.ocr["comparison"] = text_metrics
                    result.metrics.update({k: v for k, v in text_metrics.items() if not isinstance(v, list)})
                    # Word boxes are in the aligned reference frame only after registration.
                    if registered and orientation_ok:
                        defects.extend(text_defects)
                def uncertain(ocr):
                    threshold = (self.settings.paddle_min_confidence if ocr.metadata.get("engine") == "paddleocr"
                                 else self.settings.ocr_min_confidence)
                    return (ocr.confidence is None or ocr.confidence < threshold or
                            any(w.confidence < threshold for w in ocr.words) or
                            bool(ocr.metadata.get("rejected_regions")))
                for name, ocr in (("Reference", self.reference_ocr), ("Printed", printed_ocr)):
                    if uncertain(ocr):
                        result.warnings.append(f"{name} OCR confidence is low; verify the content manually.")
                        result.decision_reasons.append("Low OCR confidence; manual content review required")
                if any(uncertain(ocr) for ocr in (self.reference_ocr, printed_ocr)):
                    if "text_comparison" in result.checks:
                        result.checks["text_comparison"]["status"] = "REVIEW_REQUIRED"
                    result.metrics["text_mismatch_reliable"] = False
                else:
                    result.metrics["text_mismatch_reliable"] = True
            elif self.settings.ocr_enabled:
                self._stage(result, "text_comparison", reason="OCR unavailable or failed.")
                result.warnings.extend(dict.fromkeys(o.error for o in (self.reference_ocr, printed_ocr) if o.error))
                result.decision_reasons.append("OCR unavailable or failed; content inspection incomplete")
            else:
                self._stage(result, "text_comparison", reason="OCR disabled; visual inspection only.", required=False)
            if registered and not coverage_ok:
                result.decision_reasons.append("Page coverage is insufficient")
            required_ok = all(check["status"] == "COMPLETED" for check in result.checks.values()
                              if check.get("required"))
            result.inspection_complete = bool(registered and coverage_ok and (ocr_ok or not self.settings.ocr_enabled)
                                              and orientation_ok and required_ok)
            result.defects = merge_defects(defects, self.settings)
            raw_counts = Counter(defect.type for defect in defects)
            merged_counts = Counter(label for defect in result.defects for label in defect.details.get("labels", [defect.type]))
            text_visual_evidence = sum(len(defect.details.get("overlapping_visual_evidence", []))
                                       for defect in result.defects)
            result.metrics.update(text_error_count=len(text_defects), streak_count=len(streaks), blob_count=len(blobs),
                                  extra_ink_count=sum(d.type == "EXTRA_INK" for d in defects),
                                  missing_ink_count=sum(d.type == "MISSING_INK" for d in defects),
                                  total_defect_count=len(result.defects), raw_defect_counts=dict(raw_counts),
                                  displayed_defect_counts=dict(merged_counts),
                                  text_change_visual_evidence_count=text_visual_evidence)
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
            self._notify("saving", "RUNNING")
            saving_started = True
            for name, array in arrays.items():
                target = output / f"{name}.png"
                save_image(target, array)
                result.artifacts[name] = str(target.resolve())
        except Exception as exc:
            saving_failed = saving_started
            log.exception("Inspection failed for %s", printed_path)
            result.status = "DEFECTIVE"
            result.inspection_complete = False
            result.warnings.append(f"{type(exc).__name__}: {exc}")
            result.decision_reasons.append("Processing error; manual review required")
        result.processing_time_seconds = time.perf_counter() - started
        if output is not None:
            try:
                write_json(output / "report.json", {**result.to_dict(), "settings": self.settings.to_dict(), "schema_version": 2})
            except OSError as exc:
                log.exception("Could not save image report")
                result.status = "DEFECTIVE"
                result.inspection_complete = False
                result.warnings.append(f"Report write failed: {exc}")
                result.decision_reasons.append("Report write failure")
        self._notify("saving", "FAILED" if saving_failed or any("write fail" in r.lower() for r in result.decision_reasons) else "COMPLETED" if saving_started else "SKIPPED")
        log.info("%s: %s, %d regions, %.2fs", result.filename, result.status, len(result.defects), result.processing_time_seconds)
        return result
