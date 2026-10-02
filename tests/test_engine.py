from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from config import Settings
from src.image_loader import save_image, load_image
from src.inspection_engine import InspectionEngine
from src.models import OCRResult
from src.reporting import create_batch_dir, write_batch_report
from tools.create_test_defects import create_reference, apply_defects


class EngineTests(unittest.TestCase):
    def test_end_to_end_cache_reports_resolution_and_red_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            ref, clean, bad = root / "reference.png", root / "clean.png", root / "bad.png"
            save_image(ref, reference)
            save_image(clean, reference)
            save_image(bad, apply_defects(reference, ["vertical", "blob", "missing"]))
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=False)
            with patch("src.ocr_engine.OCREngine.extract", return_value=OCRResult(status="SUCCESS")) as extract:
                engine = InspectionEngine(settings)
                folder = create_batch_dir(root)
                good_result = engine.inspect(ref, clean, folder)
                bad_result = engine.inspect(ref, bad, folder)
                self.assertEqual(extract.call_count, 3)
            self.assertEqual(good_result.status, "PASS")
            self.assertEqual(bad_result.status, "DEFECTIVE")
            self.assertTrue(bad_result.inspection_complete)
            self.assertGreater(bad_result.metrics["streak_count"], 0)
            overlay = load_image(bad_result.artifacts["defect_overlay"])
            self.assertEqual(overlay.shape, reference.shape)
            self.assertTrue(np.any((overlay[:, :, 2] == 255) & (overlay[:, :, 1] == 0) & (overlay[:, :, 0] == 0)))
            report = json.loads((Path(bad_result.output_dir) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "DEFECTIVE")
            write_batch_report(folder, [good_result, bad_result], settings, 1.0)
            self.assertTrue((folder / "batch_report.csv").is_file())
            self.assertEqual(json.loads((folder / "batch_report.json").read_text(encoding="utf-8"))["total"], 2)

    def test_missing_ocr_and_bad_input_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "blank.png"
            save_image(image, np.full((80, 80, 3), 255, np.uint8))
            engine = InspectionEngine(replace(Settings(), ocr_enabled=False, output_dir=str(root)))
            result = engine.inspect(image, image)
            self.assertEqual(result.status, "DEFECTIVE")
            self.assertFalse(result.inspection_complete)
            self.assertTrue(any("OCR" in reason for reason in result.decision_reasons))
            result = engine.inspect(image, root / "missing.png")
            self.assertEqual(result.status, "DEFECTIVE")
            self.assertFalse(result.inspection_complete)

    def test_output_write_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "blank.png"
            save_image(path, np.full((80, 80, 3), 255, np.uint8))
            engine = InspectionEngine(replace(Settings(), ocr_enabled=False, output_dir=str(root)))
            with patch("src.inspection_engine.save_image", side_effect=PermissionError("Output is read-only")):
                result = engine.inspect(path, path)
            self.assertEqual(result.status, "DEFECTIVE")
            self.assertFalse(result.inspection_complete)
            self.assertTrue(any("read-only" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
