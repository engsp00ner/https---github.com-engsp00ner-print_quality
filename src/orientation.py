"""Quarter-turn page normalization. All angles are clockwise corrections.

Matrices map pixel centers (not outer image edges), in EXIF-corrected coordinates.
Analysis never modifies the pixels used for final inspection.
"""
import math
import time
import cv2
import numpy as np

ANGLES = (0, 90, 180, 270)
RESOLVED = {"CONFIDENT", "MANUAL"}


def rotate_page(image, angle):
    if angle not in ANGLES:
        raise ValueError("Correction must be 0, 90, 180, or 270 clockwise degrees")
    return image.copy() if angle == 0 else cv2.rotate(image, {
        90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE}[angle])


def rotation_matrix(shape, angle):
    h, w = shape[:2]
    return np.asarray({
        0: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        90: [[0, -1, h - 1], [1, 0, 0], [0, 0, 1]],
        180: [[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]],
        270: [[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]],
    }[angle], dtype=float)


def normalize(image, info):
    result = rotate_page(image, info["correction_clockwise"])
    matrix = rotation_matrix(image.shape, info["correction_clockwise"])
    info.update(original_size=list(image.shape[1::-1]), normalized_size=list(result.shape[1::-1]),
                source_frame="loaded_exif_corrected", destination_frame="normalized",
                coordinate_convention="pixel_centers",
                forward_transform=matrix.tolist(), inverse_transform=np.linalg.inv(matrix).tolist())
    return result, info


def score_words(result):
    """Text-length/confidence evidence, penalizing noise and vertical word boxes."""
    chars = 0
    weighted = 0.0
    noise = 0
    for word in result.words:
        n = sum(c.isalnum() for c in word.text)
        if not n or word.confidence < 30:
            noise += max(1, len(word.text))
            continue
        _, _, width, height = word.bbox
        geometry = min(1.0, width / max(1, height)) if n >= 3 else 1.0
        chars += n
        weighted += n * max(0, min(100, word.confidence)) * geometry
    confidence = weighted / max(1, chars)
    score = confidence / 100 * math.log1p(chars) / (1 + noise / max(1, chars))
    return {"score": score, "characters": chars, "confidence": confidence, "noise": noise}


class OrientationDetector:
    def __init__(self, settings, ocr):
        self.settings, self.ocr = settings, ocr

    def detect(self, image, override=None):
        started = time.monotonic()
        info = dict(status="AMBIGUOUS", correction_clockwise=0, method="none", candidates=[],
                    margin=0.0, warnings=[])

        def finish():
            info["elapsed_seconds"] = time.monotonic() - started
            if info["status"] not in RESOLVED:
                info["warnings"].append("Text orientation unresolved; choose a manual clockwise correction and rerun.")
            return normalize(image, info)

        if override is not None:
            if override not in ANGLES:
                raise ValueError("Invalid manual orientation")
            info.update(status="MANUAL", correction_clockwise=override, method="manual")
            return finish()
        if not self.settings.auto_orientation:
            info.update(status="UNAVAILABLE", method="disabled")
            return finish()
        if self.ocr.error:
            info.update(status="UNAVAILABLE")
            info["warnings"].append(self.ocr.error)
            return finish()
        deadline = started + self.settings.orientation_timeout
        scale = min(1.0, self.settings.orientation_max_dimension / max(image.shape[:2]))
        analysis = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
        if "osd" in self.ocr.validation.get("languages", []):
            try:
                osd = self.ocr.orientation_probe(analysis, max(0.01, (deadline - time.monotonic()) / 5))
                info["osd"] = osd
                angle = int(osd.get("rotate", -1))
                if angle in ANGLES and float(osd.get("orientation_conf", 0)) >= self.settings.orientation_osd_confidence:
                    info.update(status="CONFIDENT", method="osd", correction_clockwise=angle)
                    return finish()
            except (RuntimeError, OSError) as exc:
                info["warnings"].append(f"OSD unavailable: {exc}")
        else:
            info["warnings"].append("Optional osd.traineddata unavailable; using four-rotation OCR.")
        info["method"] = "four_rotation_ocr"
        for index, angle in enumerate(ANGLES):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Sparse text WITHOUT OSD: compare the supplied orientation consistently.
            result = self.ocr.extract(rotate_page(analysis, angle), psm=11,
                                      timeout=remaining / (4 - index))
            candidate = dict(angle=angle, **score_words(result), status=result.status)
            if result.error:
                candidate["error"] = result.error
            info["candidates"].append(candidate)
        valid = [c for c in info["candidates"] if c["status"] == "SUCCESS"]
        if len(valid) != 4:
            info.update(status="UNAVAILABLE")
            info["warnings"].append("Orientation probes incomplete or timed out; no winning angle accepted.")
        else:
            ranked = sorted(valid, key=lambda c: c["score"], reverse=True)
            best, second = ranked[:2]
            margin = (best["score"] - second["score"]) / max(best["score"], 1e-9)
            info.update(margin=margin, suggested_correction_clockwise=best["angle"])
            if best["characters"] < self.settings.orientation_min_characters:
                info["status"] = "NO_TEXT_EVIDENCE"
            elif (best["confidence"] >= self.settings.orientation_min_confidence
                  and margin >= self.settings.orientation_min_margin):
                info.update(status="CONFIDENT", correction_clockwise=best["angle"])
        return finish()
