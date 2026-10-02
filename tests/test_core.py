from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from config import Settings
from src.alignment import align
from src.defect_merging import merge_defects
from src.image_loader import load_image, save_image
from src.ink_detection import compare_ink
from src.line_detection import detect_lines
from src.models import Defect, OCRResult, OCRWord
from src.ocr_engine import validate_tesseract
from src.preprocessing import grayscale
from src.ssim_comparison import compare_ssim
from src.text_comparison import compare_text, edit_distance
from tools.create_test_defects import create_reference, apply_defects


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()

    def test_unicode_image_roundtrip_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "وزارة_الطباعة.png"
            image = np.full((32, 48, 3), 255, np.uint8)
            save_image(path, image)
            np.testing.assert_array_equal(load_image(path), image)
            path.write_text("not an image")
            with self.assertRaises(ValueError):
                load_image(path)
            with self.assertRaises(FileNotFoundError):
                load_image(Path(folder) / "missing.png")

    def test_tesseract_missing_executable(self):
        with self.assertRaisesRegex(RuntimeError, "not found"):
            validate_tesseract(replace(self.settings, tesseract_path="/definitely_missing/tesseract"))

    def test_tesseract_missing_arabic_and_valid_languages(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "tesseract.exe"
            exe.touch()
            settings = replace(self.settings, tesseract_path=str(exe))
            with patch("src.ocr_engine.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "List of available languages (1):\neng\n", "")):
                with self.assertRaisesRegex(RuntimeError, "ara.traineddata"):
                    validate_tesseract(settings)
            with patch("src.ocr_engine.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "List of available languages (2):\nara\neng\n", "")), patch("src.ocr_engine.pytesseract.pytesseract.tesseract_cmd"):
                self.assertEqual(validate_tesseract(settings)["languages"], ["ara", "eng"])

    def test_alignment_rotation_translation_and_reference_unchanged(self):
        reference = create_reference()
        snapshot = reference.copy()
        printed = apply_defects(reference, ["rotation", "shift"])
        aligned, valid, info = align(grayscale(reference), printed, self.settings)
        self.assertIn(info["status"], ["SUCCESS", "FALLBACK"])
        self.assertGreater(info["coverage"], .95)
        mask = valid > 0
        before = np.abs(grayscale(reference).astype(float) - grayscale(printed)).mean()
        after = np.abs(grayscale(reference).astype(float) - grayscale(aligned))[mask].mean()
        self.assertLess(after, before / 2)
        np.testing.assert_array_equal(snapshot, reference)
        _, _, metrics, defects = compare_ink(grayscale(reference), grayscale(aligned), valid, self.settings)
        self.assertEqual(len(defects), 0, "Resampled legitimate table lines should not become ink defects")

    def test_alignment_failure_does_not_claim_success(self):
        reference = np.full((200, 200), 255, np.uint8)
        printed = np.zeros((200, 200, 3), np.uint8)
        _, valid, info = align(reference, printed, self.settings)
        self.assertEqual(info["status"], "FAILED")
        self.assertFalse(valid.any())

    def test_ssim_identical_and_changed(self):
        reference = np.full((220, 220), 255, np.uint8)
        valid = np.full_like(reference, 255)
        score, mask, regions = compare_ssim(reference, reference, valid, self.settings)
        self.assertEqual(score, 1.0)
        self.assertFalse(mask.any())
        changed = reference.copy()
        changed[80:110, 90:130] = 0
        score, mask, regions = compare_ssim(reference, changed, valid, self.settings)
        self.assertLess(score, 1.0)
        self.assertEqual(len(regions), 1)

    def test_extra_missing_ink_noise_and_legitimate_lines(self):
        reference = np.full((300, 300), 255, np.uint8)
        reference[70:100, 70:100] = 0
        reference[250:253, 20:280] = 0
        printed = reference.copy()
        printed[70:100, 70:100] = 255
        printed[150:170, 150:170] = 0
        printed[10, 10] = 0
        extra, missing, metrics, defects = compare_ink(reference, printed, np.full_like(reference, 255), self.settings)
        self.assertEqual(metrics["extra_ink_pixels"], 400)
        self.assertEqual(metrics["missing_ink_pixels"], 900)
        self.assertEqual(extra[10, 10], 0)
        self.assertEqual(len(detect_lines(extra, missing, self.settings)), 0)
        printed[20:200, 220:224] = 0
        extra, missing, _, _ = compare_ink(reference, printed, np.full_like(reference, 255), self.settings)
        self.assertEqual(detect_lines(extra, missing, self.settings)[0].details["orientation"], "vertical")

    def test_faded_ink_retains_intensity_evidence(self):
        reference = np.full((100, 100), 255, np.uint8)
        reference[20:40, 20:60] = 0
        printed = reference.copy()
        printed[20:40, 20:60] = 140
        _, missing, metrics, _ = compare_ink(reference, printed, np.full_like(reference, 255), self.settings)
        self.assertEqual(metrics["missing_ink_pixels"], 800)

    def test_strict_arabic_numeric_differences_localized(self):
        def ocr(text):
            return OCRResult(text, text, [OCRWord(word, 90, (i*100, 20, 80, 25), (1, 1, 1, 1)) for i, word in enumerate(text.split())], 90, "SUCCESS")
        metrics, defects = compare_text(ocr("وزارة الدفاع 12584"), ocr("وزارة الدقاع 12534"), self.settings)
        self.assertEqual(metrics["character_edit_distance"], 2)
        self.assertEqual(metrics["word_edit_distance"], 2)
        self.assertEqual(len(defects), 2)
        self.assertEqual(defects[0].details["expected"], "الدفاع")
        self.assertEqual(defects[1].bbox, (200, 20, 80, 25))
        for a, b in [("أ", "ا"), ("ة", "ه"), ("ي", "ى"), ("ب", "ت")]:
            self.assertEqual(edit_distance(a, b), 1)
        metrics, defects = compare_text(ocr("one two"), ocr("one"), self.settings)
        self.assertEqual(defects[0].details["detected"], "")
        self.assertEqual(defects[0].bbox, (100, 20, 80, 25))

    def test_merge_preserves_evidence_without_giant_boxes(self):
        defects = [Defect("TEXT_ERROR", 10, 10, 40, 20, .9, {"expected": "12584"}),
                   Defect("VISUAL_DIFFERENCE", 8, 8, 46, 26), Defect("EXTRA_INK", 15, 12, 30, 16),
                   Defect("INK_BLOB", 200, 200, 15, 15)]
        merged = merge_defects(defects, self.settings)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].bbox, (10, 10, 40, 20))
        self.assertIn("EXTRA_INK", merged[0].details["labels"])
        self.assertEqual(len(merged[0].details["evidence"]), 3)


if __name__ == "__main__":
    unittest.main()
