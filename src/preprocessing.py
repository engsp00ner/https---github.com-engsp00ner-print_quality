import cv2
import numpy as np


def grayscale(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()


def for_defects(image):
    # Preserve faint ink and small character marks; no contrast normalization.
    return grayscale(image)


def for_ocr(image):
    # Tesseract performs adaptive binarization. Avoid erasing Arabic dots.
    return grayscale(image)


def ink_mask(gray, settings):
    return np.where(gray < settings.ink_threshold, 255, 0).astype(np.uint8)
