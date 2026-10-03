import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image

from config import Settings
from src.image_loader import load_image, save_image
from src.inspection_engine import InspectionEngine
from src.models import OCRResult, OCRWord
from src.ocr_engine import OrientationOCR
from src.orientation import OrientationDetector, normalize, rotate_page, rotation_matrix
from tools.create_test_defects import create_reference, apply_defects


def recognized(confidence=90):
    return OCRResult("sample readable content", diagnostic_text="sample readable content", words=[
        OCRWord(text, confidence, (i * 100, 10, 90, 20), (1, 1, 1, 1))
        for i, text in enumerate(["sample", "readable", "content"])], confidence=confidence, status="SUCCESS")


class OrientationTests(unittest.TestCase):
    def detector(self, results=None, languages=()):
        ocr = Mock(error="", validation={"languages": languages})
        ocr.extract.side_effect = results
        return OrientationDetector(Settings(), ocr)

    def test_quarter_turn_pixel_mapping_roundtrip(self):
        image = np.arange(35, dtype=np.uint8).reshape(5, 7)
        for angle in (0, 90, 180, 270):
            transformed, info = normalize(image, {"correction_clockwise": angle})
            matrix = rotation_matrix(image.shape, angle)
            for y in range(5):
                for x in range(7):
                    tx, ty, _ = (matrix @ [x, y, 1]).astype(int)
                    self.assertEqual(image[y, x], transformed[ty, tx])
                    np.testing.assert_allclose(np.asarray(info["inverse_transform"]) @ [tx, ty, 1], [x, y, 1])
            # Four inclusive corners of a word bbox map to a rectangle of swapped dimensions.
            corners = np.array([[1, 1, 1], [3, 1, 1], [3, 2, 1], [1, 2, 1]]).T
            mapped = matrix @ corners
            size = mapped[:2].max(axis=1) - mapped[:2].min(axis=1) + 1
            np.testing.assert_array_equal(size, [2, 3] if angle in (90, 270) else [3, 2])

    def test_manual_and_disabled_and_missing_runtime(self):
        image = np.zeros((5, 7), np.uint8)
        d = self.detector()
        self.assertEqual(d.detect(image, 90)[0].shape, (7, 5))
        d.ocr.extract.assert_not_called()
        d.settings = replace(Settings(), auto_orientation=False)
        self.assertEqual(d.detect(image)[1]["status"], "UNAVAILABLE")
        d.settings = Settings()
        d.ocr.error = "missing runtime"
        self.assertEqual(d.detect(image)[1]["status"], "UNAVAILABLE")
        with self.assertRaises(ValueError):
            d.detect(image, 45)

    def test_sparse_fallback_winner_and_tie(self):
        image = np.zeros((5, 7), np.uint8)
        empty = OCRResult(status="SUCCESS")
        d = self.detector([empty, recognized(), empty, empty])
        output, info = d.detect(image)
        self.assertEqual((info["status"], info["correction_clockwise"]), ("CONFIDENT", 90))
        self.assertEqual(output.shape, (7, 5))
        self.assertEqual(d.ocr.extract.call_count, 4)
        d = self.detector([recognized()] * 4)
        self.assertEqual(d.detect(image)[1]["status"], "AMBIGUOUS")
        d = self.detector([empty] * 4)
        self.assertEqual(d.detect(image)[1]["status"], "NO_TEXT_EVIDENCE")
        d = self.detector([recognized(), empty, empty, OCRResult(status="FAILED", error="timeout")])
        self.assertEqual(d.detect(image)[1]["status"], "UNAVAILABLE")

    def test_osd_and_failure_fallback(self):
        d = self.detector(languages=["osd"])
        d.ocr.orientation_probe.return_value = {"rotate": 270, "orientation_conf": 20}
        self.assertEqual(d.detect(np.zeros((5, 7), np.uint8))[1]["correction_clockwise"], 270)
        d.ocr.extract.assert_not_called()
        d = self.detector([recognized()] * 4, ["osd"])
        d.ocr.orientation_probe.side_effect = RuntimeError("Too few characters")
        self.assertEqual(d.detect(np.zeros((5, 7), np.uint8))[1]["status"], "AMBIGUOUS")

    def test_expired_budget_never_accepts_partial_candidates(self):
        d = self.detector()
        with patch("src.orientation.time.monotonic", side_effect=[0, 31, 31]):
            info = d.detect(np.zeros((5, 7), np.uint8))[1]
        self.assertEqual(info["status"], "UNAVAILABLE")
        d.ocr.extract.assert_not_called()

    def test_registration_fallback_requires_unique_geometric_match(self):
        settings = replace(Settings(), ocr_enabled=True)
        engine = InspectionEngine(settings, 0)
        pixels = np.zeros((40, 60, 3), np.uint8)
        pixels[10:30, 15:45] = 255
        engine.reference_gray = pixels[:, :, 0]
        engine.reference_features = None
        engine.reference_orientation = {"status": "MANUAL"}
        valid = np.full((40, 60), 255, np.uint8)
        def unresolved(*args):
            return normalize(pixels, {"status": "AMBIGUOUS", "correction_clockwise": 0,
                                      "warnings": [], "elapsed_seconds": 0})
        engine.orientation_detector.detect = Mock(side_effect=unresolved)
        bad = (255 - pixels, valid, {"status": "SUCCESS", "coverage": 1.0})
        good = (pixels, valid, {"status": "SUCCESS", "coverage": 1.0})
        printed = pixels.copy()
        printed[0, 0] = 255  # Exercise fallback rather than the identical-page fast path.
        with patch("src.inspection_engine.align", side_effect=[bad, good, bad, bad]):
            _, info = engine._printed_orientation(printed, Path("page.png"))
        self.assertEqual((info["status"], info["correction_clockwise"]), ("CONFIDENT", 90))
        with patch("src.inspection_engine.align", side_effect=[good] * 4):
            _, info = engine._printed_orientation(printed, Path("page.png"))
        self.assertEqual(info["status"], "AMBIGUOUS")

    def test_exif_is_applied_once_then_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "exif.png"
            pixels = np.arange(20 * 30 * 3, dtype=np.uint8).reshape(20, 30, 3)
            im = Image.fromarray(pixels)
            exif = Image.Exif()
            exif[274] = 6
            im.save(path, exif=exif)
            loaded = load_image(path)
            self.assertEqual(loaded.shape, (30, 20, 3))
            normalized, _ = self.detector().detect(loaded, 0)
            np.testing.assert_array_equal(normalized, loaded)

    def test_runtime_restored_on_osd_and_ocr_failure(self):
        with patch("src.ocr_engine.validate_tesseract", return_value={"executable": "fake", "languages": ["osd"]}):
            engine = OrientationOCR(replace(Settings(), tessdata_dir="temporary models", ocr_enabled=True))
        before = os.environ.get("TESSDATA_PREFIX")
        import pytesseract
        command = pytesseract.pytesseract.tesseract_cmd
        for method in ("image_to_osd", "image_to_data"):
            with patch(f"src.ocr_engine.pytesseract.{method}", side_effect=RuntimeError("timeout")):
                if method == "image_to_osd":
                    with self.assertRaises(RuntimeError):
                        engine.orientation_probe(np.zeros((20, 20), np.uint8), 1)
                else:
                    self.assertEqual(engine.extract(np.zeros((20, 20), np.uint8)).status, "FAILED")
            self.assertEqual(os.environ.get("TESSDATA_PREFIX"), before)
            self.assertEqual(pytesseract.pytesseract.tesseract_cmd, command)

    def test_inspection_all_manual_rotation_pairs_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upright = create_reference()
            paths = {}
            for angle in (0, 90, 180, 270):
                paths[angle] = root / f"rotated_{angle}.png"
                save_image(paths[angle], rotate_page(upright, angle))
            settings = replace(Settings(), ocr_enabled=False, output_dir=folder)
            with patch("src.ocr_engine.OCREngine.extract", return_value=recognized()) as extract:
                for reference_angle, reference in paths.items():
                    engine = InspectionEngine(settings, (-reference_angle) % 360,
                                              {p: (-a) % 360 for a, p in paths.items()})
                    for angle, path in paths.items():
                        result = engine.inspect(reference, path)
                        self.assertEqual(result.status, "PASS", result.decision_reasons)
                        self.assertTrue(result.inspection_complete)
                        np.testing.assert_array_equal(load_image(result.artifacts["normalized_printed"]), upright)
                        np.testing.assert_allclose(result.alignment["loaded_printed_to_reference"],
                                                   rotation_matrix(rotate_page(upright, angle).shape, (-angle) % 360))
                self.assertEqual(extract.call_count, 0)
                engine.reference_orientation_override = 180
                engine.prepare_reference(reference)
                self.assertEqual(extract.call_count, 0)

    def test_ambiguous_orientation_cannot_pass_even_if_ocr_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "blank.png"
            save_image(p, np.full((80, 80, 3), 255, np.uint8))
            with patch("src.ocr_engine.OCREngine.extract", return_value=recognized()):
                r = InspectionEngine(replace(Settings(), ocr_enabled=True, tesseract_path="missing-runtime", output_dir=folder)).inspect(p, p)
                self.assertFalse(r.inspection_complete)
                self.assertEqual(r.status, "DEFECTIVE")
                self.assertTrue(any("orientation unresolved" in s for s in r.decision_reasons))

    def test_rotated_defects_remain_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ref = root / "ref.png"
            bad = root / "bad.png"
            upright = create_reference()
            save_image(ref, upright)
            settings = replace(Settings(), ocr_enabled=False, output_dir=folder)
            with patch("src.ocr_engine.OCREngine.extract", return_value=recognized()):
                for angle in (0, 90, 180, 270):
                    save_image(bad, rotate_page(apply_defects(upright, ["vertical", "blob", "missing"]), angle))
                    r = InspectionEngine(settings, 0, {bad: (-angle) % 360}).inspect(ref, bad)
                    self.assertEqual(r.status, "DEFECTIVE")
                    self.assertTrue(r.inspection_complete)
                    self.assertGreater(r.metrics["streak_count"], 0)
                    self.assertGreater(len(r.defects), 0)


if __name__ == "__main__":
    unittest.main()
