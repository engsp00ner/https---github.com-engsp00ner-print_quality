import cv2
import numpy as np
from skimage.metrics import structural_similarity
from .regions import filter_mask, regions


def compare_ssim(reference, printed, valid, settings):
    _, similarity = structural_similarity(reference, printed, data_range=255, full=True)
    # SSIM has a 7x7 neighbourhood; exclude windows touching an invalid warp edge.
    interior = cv2.erode(valid, np.ones((7, 7), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    score = float(similarity[interior].mean()) if interior.any() else None
    delta = cv2.absdiff(reference, printed)
    mask = np.where(interior & ((1 - similarity) > settings.ssim_difference_threshold)
                    & (delta >= settings.intensity_difference), 255, 0).astype(np.uint8)
    kernel = np.ones((settings.component_close_size, settings.component_close_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask[~interior] = 0
    mask = filter_mask(mask, settings.min_defect_area)
    return score, mask, regions(mask, "VISUAL_DIFFERENCE", settings.min_defect_area)
