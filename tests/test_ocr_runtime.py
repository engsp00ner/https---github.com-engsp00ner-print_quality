"""Optional live OCR check; assertions do not depend on exact recognized text."""
from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from config import load_settings
from src.ocr_engine import OCREngine, OrientationOCR, validate_tesseract
from src.orientation import OrientationDetector, rotate_page, RESOLVED
import numpy as np
from tools.create_test_defects import create_reference


class OCRRuntimeTests(unittest.TestCase):
    def test_live_mixed_script_orientation_all_quarter_turns(self):
        settings = load_settings()
        ocr = OrientationOCR(settings)
        if ocr.error:
            self.skipTest(ocr.error)
        upright = create_reference()
        detector = OrientationDetector(settings, ocr)
        for angle in (0, 90, 180, 270):
            normalized, info = detector.detect(rotate_page(upright, angle))
            self.assertIn(info["status"], RESOLVED, info)
            self.assertEqual(info["correction_clockwise"], (-angle) % 360)
            np.testing.assert_array_equal(normalized, upright)

    def test_real_ocr_with_spaces_in_data_path_and_environment_restored(self):
        settings = load_settings()
        try:
            validation = validate_tesseract(settings)
        except Exception as exc:
            self.skipTest(f"Real Tesseract not configured: {exc}")
        data_dir = Path(settings.tessdata_dir) if settings.tessdata_dir else Path(validation["executable"]).parent / "tessdata"
        with tempfile.TemporaryDirectory(prefix="ocr data with spaces ") as folder:
            for language in settings.ocr_languages.split("+"):
                source = data_dir / f"{language}.traineddata"
                if not source.is_file():
                    self.skipTest("Cannot locate model files for the path-with-spaces test")
                shutil.copy2(source, Path(folder) / source.name)
            before = os.environ.get("TESSDATA_PREFIX")
            engine = OrientationOCR(replace(settings, tessdata_dir=folder, ocr_enabled=True))
            result = engine.extract(create_reference())
            self.assertEqual(result.status, "SUCCESS", result.error)
            self.assertTrue(result.words)
            self.assertIsNotNone(result.confidence)
            self.assertEqual(os.environ.get("TESSDATA_PREFIX"), before)


if __name__ == "__main__":
    unittest.main()
