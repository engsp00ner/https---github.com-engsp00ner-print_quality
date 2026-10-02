import cv2
from .regions import regions


def detect_lines(extra_mask, missing_mask, settings):
    defects = []
    for polarity, mask in (("extra", extra_mask), ("missing", missing_mask)):
        for orientation, size in (("horizontal", (settings.min_line_length, 1)),
                                  ("vertical", (1, settings.min_line_length))):
            opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))
            for region in regions(opened, "STREAK", settings.min_defect_area):
                x, y, w, h = region.bbox
                if max(w, h) / max(1, min(w, h)) < settings.line_aspect_ratio:
                    continue
                start, end = ((x, y + h // 2), (x + w - 1, y + h // 2)) if orientation == "horizontal" else ((x + w // 2, y), (x + w // 2, y + h - 1))
                region.details.update(orientation=orientation, polarity=polarity,
                                      start=list(start), end=list(end), length=max(w, h))
                defects.append(region)
    return defects
