import cv2
import numpy as np
from .preprocessing import ink_mask
from .regions import filter_mask, regions


def compare_ink(reference, printed, valid, settings, reference_ink=None):
    ref = reference_ink if reference_ink is not None else ink_mask(reference, settings)
    prn = ink_mask(printed, settings)
    radius = settings.registration_tolerance
    kernel = np.ones((radius * 2 + 1, radius * 2 + 1), np.uint8)
    nearby_ref, nearby_prn = cv2.dilate(ref, kernel), cv2.dilate(prn, kernel)
    signed = reference.astype(np.int16) - printed.astype(np.int16)
    # Pale smudges on blank paper can sit above the binary ink threshold.
    # Keep their signed intensity evidence, while retaining the same spatial
    # tolerance around legitimate strokes and the noise-area filter below.
    extra = (nearby_ref == 0) & (signed >= settings.intensity_difference)
    missing = ((ref > 0) & (nearby_prn == 0) & (-signed >= settings.intensity_difference))
    # Intensity evidence also catches fading/bleeding inside existing ink strokes.
    # Compare the darkest nearby sample for intensity evidence. Resampling can
    # lighten a stroke edge without fading its core; do not call that missing ink.
    darkest_reference = cv2.erode(reference, kernel).astype(np.int16)
    darkest_printed = cv2.erode(printed, kernel).astype(np.int16)
    extra |= (ref > 0) & (prn > 0) & ((darkest_reference - printed.astype(np.int16)) >= settings.ink_density_difference)
    missing |= (ref > 0) & (prn > 0) & ((darkest_printed - reference.astype(np.int16)) >= settings.ink_density_difference)
    extra = np.where(extra & (valid > 0), 255, 0).astype(np.uint8)
    missing = np.where(missing & (valid > 0), 255, 0).astype(np.uint8)
    # Report raw evidence separately from area-filtered decisions.
    raw_extra, raw_missing = int(np.count_nonzero(extra)), int(np.count_nonzero(missing))
    extra = filter_mask(extra, settings.min_defect_area)
    missing = filter_mask(missing, settings.min_defect_area)
    ep, mp, denominator = int(np.count_nonzero(extra)), int(np.count_nonzero(missing)), max(1, int(np.count_nonzero(valid)))
    metrics = {"extra_ink_pixels": ep, "missing_ink_pixels": mp,
               "extra_ink_ratio": ep / denominator, "missing_ink_ratio": mp / denominator,
               "raw_extra_ink_pixels": raw_extra, "raw_missing_ink_pixels": raw_missing,
               "compared_pixels": denominator}
    return extra, missing, metrics, regions(extra, "EXTRA_INK", settings.min_defect_area) + regions(missing, "MISSING_INK", settings.min_defect_area)
