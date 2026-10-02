import cv2
import numpy as np
from .models import Defect


def filter_mask(mask, minimum_area):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros(count, np.uint8)
    keep[1:] = np.where(stats[1:, cv2.CC_STAT_AREA] >= minimum_area, 255, 0)
    return keep[labels]


def regions(mask, category, minimum_area):
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    return [Defect(category, *(int(n) for n in stats[i, :4]), details={
        "area": int(stats[i, cv2.CC_STAT_AREA]), "center": centers[i].tolist()})
        for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= minimum_area]
