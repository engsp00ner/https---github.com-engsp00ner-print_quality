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
from src.models import OCRResult, OCRWord
from src.reporting import create_batch_dir, write_batch_report
from tools.create_test_defects import create_reference, apply_defects


class EngineTests(unittest.TestCase):
    @staticmethod
    def ocr(text="reference text", confidence=90):
        words = [OCRWord(word, confidence, (20 + index * 100, 20, 80, 24), (1, 1, 1, 1))
                 for index, word in enumerate(text.split())]
        return OCRResult(text, text, words, confidence, "SUCCESS")

    @staticmethod
    def inspect(settings, reference, printed, ocr_results):
        return InspectionEngine(settings, 0, {printed: 0}), patch(
            "src.ocr_engine.OCREngine.extract", side_effect=ocr_results)
    def test_end_to_end_cache_reports_resolution_and_red_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            ref, clean, bad = root / "reference.png", root / "clean.png", root / "bad.png"
            save_image(ref, reference)
            save_image(clean, reference)
            save_image(bad, apply_defects(reference, ["vertical", "blob", "missing"]))
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=True)
            with patch("src.ocr_engine.OCREngine.extract", return_value=self.ocr()) as extract:
                engine = InspectionEngine(settings, 0, {clean: 0, bad: 0})
                folder = create_batch_dir(root)
                good_result = engine.inspect(ref, clean, folder)
                bad_result = engine.inspect(ref, bad, folder)
                self.assertEqual(extract.call_count, 3)
            self.assertEqual(good_result.status, "PASS")
            self.assertEqual(bad_result.status, "DEFECTIVE")
            self.assertTrue(bad_result.inspection_complete)
            self.assertGreater(bad_result.metrics["streak_count"], 0)
            overlay = load_image(bad_result.artifacts["defect_overlay"])
            self.assertEqual(overlay.shape, load_image(bad_result.artifacts["normalized_reference"]).shape)
            self.assertGreaterEqual(overlay.shape[0], reference.shape[0])
            self.assertGreaterEqual(overlay.shape[1], reference.shape[1])
            self.assertTrue(np.any((overlay[:, :, 2] == 255) & (overlay[:, :, 1] == 0) & (overlay[:, :, 0] == 0)))
            report = json.loads((Path(bad_result.output_dir) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "DEFECTIVE")
            write_batch_report(folder, [good_result, bad_result], settings, 1.0)
            self.assertTrue((folder / "batch_report.csv").is_file())
            self.assertEqual(json.loads((folder / "batch_report.json").read_text(encoding="utf-8"))["total"], 2)

    def test_text_and_separate_extra_ink_are_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            printed = reference.copy()
            printed[900:940, 900:960] = 0  # separate from synthetic text word boxes
            ref, sample = root / "reference.png", root / "sample.png"
            save_image(ref, reference)
            save_image(sample, printed)
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=True)
            engine = InspectionEngine(settings, 0, {sample: 0})
            with patch("src.ocr_engine.OCREngine.extract", side_effect=[self.ocr("Order 12584"), self.ocr("Order 12534")]):
                result = engine.inspect(ref, sample)
            self.assertTrue(result.inspection_complete, result.decision_reasons)
            self.assertGreater(result.metrics["text_error_count"], 0)
            self.assertGreater(result.metrics["extra_ink_count"], 0)
            self.assertEqual(result.checks["text_comparison"]["status"], "COMPLETED")
            self.assertEqual(result.checks["ink"]["status"], "COMPLETED")
            self.assertIn("TEXT_ERROR", result.metrics["displayed_defect_counts"])
            self.assertIn("EXTRA_INK", result.metrics["displayed_defect_counts"])
            report = json.loads((Path(result.output_dir) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["checks"]["ink"]["status"], "COMPLETED")
            self.assertIn("EXTRA_INK", report["metrics"]["displayed_defect_counts"])

    def test_text_only_and_visual_only_checks_run_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            ref, text_only, extra_only = root / "ref.png", root / "text.png", root / "extra.png"
            save_image(ref, reference)
            save_image(text_only, reference)
            extra = reference.copy()
            extra[900:940, 900:960] = 0
            save_image(extra_only, extra)
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=True)
            with patch("src.ocr_engine.OCREngine.extract", side_effect=[self.ocr("A 1"), self.ocr("A 2")]):
                text = InspectionEngine(settings, 0, {text_only: 0}).inspect(ref, text_only)
            self.assertGreater(text.metrics["text_error_count"], 0)
            self.assertEqual(text.metrics["extra_ink_count"], 0)
            self.assertEqual(text.checks["ink"]["status"], "COMPLETED")
            with patch("src.ocr_engine.OCREngine.extract", return_value=self.ocr()):
                visual = InspectionEngine(settings, 0, {extra_only: 0}).inspect(ref, extra_only)
            self.assertEqual(visual.metrics["text_error_count"], 0)
            self.assertGreater(visual.metrics["extra_ink_count"], 0)
            self.assertEqual(visual.checks["text_comparison"]["status"], "COMPLETED")

    def test_text_mismatch_plus_fading_and_low_confidence_is_review_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            reference[900:940, 900:960] = 0
            faded = reference.copy()
            faded[900:940, 900:960] = 140
            ref, path = root / "ref.png", root / "faded.png"
            save_image(ref, reference)
            save_image(path, faded)
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=True)
            with self.assertLogs("src.inspection_engine", "INFO") as logs, \
                 patch("src.ocr_engine.OCREngine.extract", side_effect=[self.ocr("Order 12584", 20), self.ocr("Order 12534", 20)]):
                result = InspectionEngine(settings, 0, {path: 0}).inspect(ref, path)
            self.assertGreater(result.metrics["text_error_count"], 0)
            self.assertGreater(result.metrics["missing_ink_count"], 0)
            self.assertEqual(result.checks["text_comparison"]["status"], "REVIEW_REQUIRED")
            self.assertFalse(result.metrics["text_mismatch_reliable"])
            self.assertFalse(result.inspection_complete)
            self.assertTrue(any("Check ink completed" in line for line in logs.output))

    def test_ocr_failure_and_visual_or_ssim_failure_do_not_erase_other_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            sample = reference.copy()
            sample[900:940, 900:960] = 0
            ref, path = root / "ref.png", root / "sample.png"
            save_image(ref, reference)
            save_image(path, sample)
            settings = replace(Settings(), output_dir=str(root), ocr_enabled=True)
            with patch("src.ocr_engine.OCREngine.extract", side_effect=[self.ocr(), OCRResult(status="FAILED", error="test OCR failure")]):
                failed_ocr = InspectionEngine(settings, 0, {path: 0}).inspect(ref, path)
            self.assertFalse(failed_ocr.inspection_complete)
            self.assertEqual(failed_ocr.checks["ink"]["status"], "COMPLETED")
            self.assertGreater(failed_ocr.metrics["extra_ink_count"], 0)
            with patch("src.ocr_engine.OCREngine.extract", return_value=self.ocr()), \
                 patch("src.inspection_engine.compare_ssim", side_effect=RuntimeError("test SSIM failure")):
                failed_ssim = InspectionEngine(settings, 0, {path: 0}).inspect(ref, path)
            self.assertFalse(failed_ssim.inspection_complete)
            self.assertEqual(failed_ssim.checks["ssim"]["status"], "FAILED")
            self.assertEqual(failed_ssim.checks["ink"]["status"], "COMPLETED")
            self.assertGreater(failed_ssim.metrics["extra_ink_count"], 0)

    def test_failed_alignment_skips_pixel_checks_without_confirmed_visual_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = create_reference()
            ref, path = root / "ref.png", root / "sample.png"
            save_image(ref, reference)
            save_image(path, apply_defects(reference, ["vertical", "blob"]))
            failed_alignment = {"status": "FAILED", "warnings": ["test alignment failure"], "coverage": 0.0}
            with patch("src.ocr_engine.OCREngine.extract", return_value=self.ocr()), \
                 patch("src.inspection_engine.align", return_value=(reference, np.zeros(reference.shape[:2], np.uint8), failed_alignment)):
                result = InspectionEngine(replace(Settings(), output_dir=str(root), ocr_enabled=True), 0, {path: 0}).inspect(ref, path)
            self.assertFalse(result.inspection_complete)
            self.assertEqual(result.checks["ink"]["status"], "SKIPPED")
            self.assertEqual(result.metrics["extra_ink_count"], 0)
            self.assertEqual(result.metrics["missing_ink_count"], 0)

    def test_disabled_ocr_can_pass_visual_checks_but_bad_input_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "blank.png"
            save_image(image, np.full((80, 80, 3), 255, np.uint8))
            engine = InspectionEngine(replace(Settings(), ocr_enabled=False, output_dir=str(root)))
            result = engine.inspect(image, image)
            self.assertEqual(result.status, "PASS")
            self.assertTrue(result.inspection_complete)
            self.assertEqual(result.checks["text_comparison"]["status"], "SKIPPED")
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
