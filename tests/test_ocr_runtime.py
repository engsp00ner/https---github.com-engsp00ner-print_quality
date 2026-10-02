"""Optional live OCR check; assertions do not depend on exact recognized text."""
from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from config import load_settings
from src.ocr_engine import OCREngine, validate_tesseract
from tools.create_test_defects import create_reference


class OCRRuntimeTests(unittest.TestCase):
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
            engine = OCREngine(replace(settings, tessdata_dir=folder, ocr_enabled=True))
            result = engine.extract(create_reference())
            self.assertEqual(result.status, "SUCCESS", result.error)
            self.assertTrue(result.words)
            self.assertIsNotNone(result.confidence)
            self.assertEqual(os.environ.get("TESSDATA_PREFIX"), before)


if __name__ == "__main__":
    unittest.main()
