"""Regressions for optional OCR and ink at the physical scan boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from config import Settings
from src.alignment import full_page_frame, geometric_orientation, prepare_features
from src.image_loader import save_image
from src.ink_detection import compare_ink
from src.inspection_engine import InspectionEngine
from src.orientation import rotate_page
from src.preprocessing import grayscale
from tools.create_test_defects import create_reference


class VisualModeTests(unittest.TestCase):
    def test_no_ocr_runtime_or_extraction_when_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.png"
            save_image(path, create_reference())
            settings = Settings(ocr_enabled=False, output_dir=folder,
                                tesseract_path="missing", paddle_python="missing")
            with patch("src.ocr_engine.validate_tesseract", side_effect=AssertionError("OCR validation")), \
                 patch("src.ocr_engine.OCREngine.extract", side_effect=AssertionError("OCR extraction")), \
                 patch("src.ocr_engine.OrientationOCR.extract", side_effect=AssertionError("Orientation OCR")):
                result = InspectionEngine(settings).inspect(path, path)
            self.assertEqual(result.status, "PASS", result.decision_reasons)
            self.assertTrue(result.inspection_complete)
            self.assertEqual(result.metrics["inspection_scope"], "visual_only")
            for stage in ("reference_ocr", "printed_ocr", "text_comparison"):
                self.assertEqual(result.checks[stage]["status"], "SKIPPED")
                self.assertFalse(result.checks[stage]["required"])

    def test_geometry_resolves_all_quarter_turns_without_ocr(self):
        reference = create_reference()
        gray = grayscale(reference)
        settings = Settings()
        features = prepare_features(gray, settings)
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                info = geometric_orientation(gray, rotate_page(reference, angle), settings, features)
                self.assertIsNotNone(info)
                self.assertEqual(info["correction_clockwise"], (-angle) % 360)
        self.assertIsNone(geometric_orientation(gray, np.full_like(reference, 255), settings, features))

    def test_all_four_corners_outside_reference_are_retained(self):
        reference = np.full((200, 200, 3), 255, np.uint8)
        printed = np.full((240, 240, 3), 255, np.uint8)
        for y in (0, 230):
            for x in (0, 230):
                printed[y:y + 10, x:x + 10] = 0
        matrix = np.array([[1., 0, -20], [0, 1, -20], [0, 0, 1]])
        aligned = cv2.warpPerspective(printed, matrix, (200, 200))
        valid = np.full((200, 200), 255, np.uint8)
        info = {"homography": matrix.tolist(), "coverage": 1.0}
        ref, sample, mask, offset = full_page_frame(reference, printed, aligned, valid, info)
        self.assertEqual(offset, (20, 20))
        self.assertEqual(sample.shape, printed.shape)
        extra, _, metrics, defects = compare_ink(grayscale(ref), grayscale(sample), mask, Settings())
        self.assertEqual(metrics["extra_ink_pixels"], 400)
        self.assertEqual(len(defects), 4)
        self.assertEqual(info["coverage"], 1.0)
        np.testing.assert_allclose(info["homography"], np.eye(3))
        self.assertTrue(extra[0, 0] and extra[-1, -1])

    def test_faint_margin_smudge_above_ink_threshold_is_detected(self):
        reference = np.full((200, 200), 255, np.uint8)
        printed = reference.copy()
        printed[:20, :20] = 200
        printed[100, 100] = 200  # Isolated scanner noise is still rejected.
        extra, _, metrics, defects = compare_ink(reference, printed, np.full_like(reference, 255), Settings())
        self.assertEqual(metrics["extra_ink_pixels"], 400)
        self.assertEqual(len(defects), 1)
        self.assertEqual(extra[100, 100], 0)

    def test_low_coverage_still_reports_visible_ink_without_passing(self):
        with tempfile.TemporaryDirectory() as folder:
            ref, sample = Path(folder) / "ref.png", Path(folder) / "sample.png"
            reference = create_reference()
            printed = reference.copy()
            printed[900:940, 900:960] = 0
            save_image(ref, reference)
            save_image(sample, printed)
            valid = np.full(reference.shape[:2], 255, np.uint8)
            info = {"status": "SUCCESS", "coverage": .9, "warnings": []}
            with patch("src.inspection_engine.align", return_value=(printed, valid, info)):
                result = InspectionEngine(Settings(output_dir=folder), 0, {sample: 0}).inspect(ref, sample)
            self.assertFalse(result.inspection_complete)
            self.assertGreater(result.metrics["extra_ink_count"], 0)
            self.assertEqual(result.checks["ink"]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
