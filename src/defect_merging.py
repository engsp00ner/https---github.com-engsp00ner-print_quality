from copy import deepcopy

PRIORITY = {"TEXT_ERROR": 0, "STREAK": 1, "INK_BLOB": 2, "EXTRA_INK": 3,
            "MISSING_INK": 3, "VISUAL_DIFFERENCE": 4}
TEXT_OVERLAP_EVIDENCE = {"EXTRA_INK", "MISSING_INK", "VISUAL_DIFFERENCE"}


def overlap(a, b):
    x = max(a.x, b.x)
    y = max(a.y, b.y)
    right = min(a.x + a.width, b.x + b.width)
    bottom = min(a.y + a.height, b.y + b.height)
    intersection = max(0, right - x) * max(0, bottom - y)
    aa, ba = a.width * a.height, b.width * b.height
    return intersection / max(1, aa + ba - intersection), intersection / max(1, min(aa, ba))


def merge_defects(defects, settings):
    merged = []
    for source in sorted(defects, key=lambda d: (PRIORITY.get(d.type, 9), d.width * d.height)):
        defect = deepcopy(source)
        evidence = {"type": defect.type, "bbox": list(defect.bbox), "confidence": defect.confidence,
                    "details": deepcopy(defect.details)}
        matches = [existing for existing in merged
                   if (lambda scores: scores[0] >= settings.merge_iou or scores[1] >= settings.merge_containment)(overlap(existing, defect))]
        if matches:
            # Keep specific boxes rather than expanding them to large SSIM contours.
            # A broad contour can support several distinct local defects.
            for existing in matches:
                existing.details.setdefault("labels", [existing.type])
                # Changed glyphs naturally create extra/missing/different pixels.
                # Retain that evidence, but do not promote it into a second confirmed
                # print-quality classification when it overlaps a text mismatch.
                text_overlap = (existing.type == "TEXT_ERROR" and defect.type in TEXT_OVERLAP_EVIDENCE)
                if not text_overlap and defect.type not in existing.details["labels"]:
                    existing.details["labels"].append(defect.type)
                if text_overlap:
                    evidence["classification"] = "text_change_visual_evidence"
                    existing.details.setdefault("overlapping_visual_evidence", []).append(defect.type)
                existing.details.setdefault("evidence", []).append(evidence)
        else:
            defect.details["labels"] = [defect.type]
            defect.details["evidence"] = [evidence]
            merged.append(defect)
    return sorted(merged, key=lambda d: (d.y, d.x))
