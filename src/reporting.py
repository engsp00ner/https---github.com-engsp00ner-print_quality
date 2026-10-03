import csv
from datetime import datetime
import json
from pathlib import Path
import re
import uuid


def write_json(path, payload):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def create_batch_dir(root):
    path = Path(root) / f"batch_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


def create_image_dir(batch_dir, image_path):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(image_path).stem)[:80].rstrip(". ") or "page"
    path = Path(batch_dir) / f"page_{stem}_{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


CSV_FIELDS = ["filename", "printed_path", "status", "inspection_complete", "alignment_status", "ssim",
              "text_similarity", "character_error_rate", "word_error_rate", "ocr_reference_confidence",
              "ocr_printed_confidence", "text_error_count", "extra_ink_pixels", "extra_ink_ratio",
              "missing_ink_pixels", "missing_ink_ratio", "streak_count", "blob_count", "total_defect_count",
              "processing_time_seconds", "output_dir", "warnings", "decision_reasons",
              "reference_orientation_status", "reference_correction_clockwise",
              "printed_orientation_status", "printed_correction_clockwise", "check_statuses",
              "raw_defect_counts", "displayed_defect_counts", "text_mismatch_reliable"]


def _csv_safe(value):
    # Prevent filenames/OCR-controlled values being interpreted as spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_csv(path, results):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = result.to_dict()
            row.update(result.metrics)
            row["check_statuses"] = json.dumps({name: check.get("status") for name, check in result.checks.items()},
                                               ensure_ascii=False, sort_keys=True)
            for name in ("reference", "printed"):
                info = result.orientation.get(name, {})
                row[f"{name}_orientation_status"] = info.get("status", "UNAVAILABLE")
                row[f"{name}_correction_clockwise"] = info.get("correction_clockwise", "")
            row.update(alignment_status=result.alignment.get("status", "FAILED"),
                       total_defect_count=len(result.defects), warnings=" | ".join(result.warnings),
                       decision_reasons=" | ".join(result.decision_reasons))
            writer.writerow({key: _csv_safe(value) for key, value in row.items() if key in CSV_FIELDS})


def write_batch_report(folder, results, settings, duration, cancelled=False):
    write_json(Path(folder) / "batch_report.json", {
        "schema_version": 2, "created_at": datetime.now().astimezone().isoformat(),
        "settings": settings.to_dict(), "duration_seconds": duration, "cancelled": cancelled,
        "total": len(results), "pass": sum(r.status == "PASS" for r in results),
        "defective": sum(r.status == "DEFECTIVE" for r in results),
        "incomplete": sum(not r.inspection_complete for r in results),
        "results": [r.to_dict() for r in results]})
    export_csv(Path(folder) / "batch_report.csv", results)
