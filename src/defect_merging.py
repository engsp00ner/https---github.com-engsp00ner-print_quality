from copy import deepcopy

PRIORITY = {"TEXT_ERROR": 0, "STREAK": 1, "INK_BLOB": 2, "EXTRA_INK": 3,
            "MISSING_INK": 3, "VISUAL_DIFFERENCE": 4}


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
                if defect.type not in existing.details["labels"]:
                    existing.details["labels"].append(defect.type)
                existing.details.setdefault("evidence", []).append(evidence)
        else:
            defect.details["labels"] = [defect.type]
            defect.details["evidence"] = [evidence]
            merged.append(defect)
    return sorted(merged, key=lambda d: (d.y, d.x))
