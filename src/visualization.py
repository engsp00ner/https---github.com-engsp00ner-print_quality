import cv2

LABELS = {"TEXT_ERROR": "TEXT ERROR", "EXTRA_INK": "EXTRA INK", "MISSING_INK": "MISSING PRINT",
          "STREAK": "STREAK", "INK_BLOB": "INK BLOB", "VISUAL_DIFFERENCE": "VISUAL DEFECT"}


def annotate(image, defects):
    overlay = image.copy()
    height, width = overlay.shape[:2]
    thickness = max(2, round(max(height, width) / 1000))
    font_scale = max(0.45, min(1.2, max(height, width) / 2200))
    for i, defect in enumerate(defects, 1):
        x, y = max(0, defect.x), max(0, defect.y)
        right, bottom = min(width - 1, defect.x + defect.width), min(height - 1, defect.y + defect.height)
        cv2.rectangle(overlay, (x, y), (right, bottom), (0, 0, 255), thickness)
        label = f"{i} {LABELS.get(defect.type, defect.type)}"
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        lx = max(0, min(x, width - tw - 4))
        ly = y - 5 if y > th + 8 else min(height - baseline - 1, bottom + th + 5)
        cv2.rectangle(overlay, (lx, ly - th - 2), (min(width - 1, lx + tw + 3), ly + baseline), (255, 255, 255), -1)
        cv2.putText(overlay, label, (lx + 1, ly), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), 1, cv2.LINE_AA)
    return overlay
