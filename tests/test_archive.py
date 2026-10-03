"""Synthetic persistence fixtures; no detector accuracy claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
from config import Settings
from src.models import InspectionResult, Defect
from src.inspection_session import InspectionSession, evidence_rows, timestamp
from src.inspection_archive import save_inspection, load_inspection


def synthetic_session(root):
    source = root / "source.png"
    Image.new("RGB", (800, 1000), "white").save(source)
    session = InspectionSession.create(source, [source], Settings())
    result = InspectionResult("source.png", str(source), str(source), inspection_complete=True)
    result.artifacts = {"aligned_printed": str(source), "normalized_reference": str(source), "original_printed": str(source)}
    result.defects = [Defect("EXTRA_INK", 130, 220, 80, 50, details={"labels": ["EXTRA_INK"]})]
    result.metrics = {"ssim": .9}
    result.ocr = {"printed": {"strict_text": "نص عربي 123 mixed"}, "reference": {"strict_text": "نص عربي"}}
    result.checks = {"alignment": {"status": "COMPLETED"}}
    data = result.to_dict()
    data["evidence_rows"] = evidence_rows(data)
    session.samples[0].update(state="Defective", result=data)
    session.status = "Completed"
    session.finished_at = timestamp()
    return session


class ArchiveTests(unittest.TestCase):
    def test_full_page_frame_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            result = session.samples[0]["result"]
            result["orientation"].update(comparison_frame="full_page", reference_offset=[20, 30])
            result["evidence_rows"] = evidence_rows(result)
            path = root / "full_page.pinspect"
            save_inspection(path, session)
            loaded = load_inspection(path)
            self.assertEqual(loaded.samples[0]["result"]["evidence_rows"][0]["frame"], "full_page")
            self.assertEqual(loaded.samples[0]["result"]["orientation"]["reference_offset"], [20, 30])
            loaded.storage.cleanup()

    def test_portable_after_source_removed_and_analysis_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            path = root / "saved.pinspect"
            save_inspection(path, session)
            (root / "source.png").rename(root / "moved.png")
            with patch("src.inspection_engine.InspectionEngine.__init__", side_effect=AssertionError("Analysis called")), \
                 patch("src.ocr_engine.OCREngine.__init__", side_effect=AssertionError("OCR initialized")), \
                 patch("src.orientation.OrientationDetector.detect", side_effect=AssertionError("Orientation called")), \
                 patch("src.alignment.align", side_effect=AssertionError("Alignment called")):
                loaded = load_inspection(path)
            self.assertEqual(loaded.samples[0]["result"]["evidence_rows"], session.samples[0]["result"]["evidence_rows"])
            self.assertEqual(loaded.samples[0]["result"]["ocr"], session.samples[0]["result"]["ocr"])
            self.assertEqual(loaded.samples[0]["result"]["metrics"], session.samples[0]["result"]["metrics"])
            self.assertTrue(Path(loaded.reference).is_file())
            self.assertTrue(Path(loaded.samples[0]["result"]["artifacts"]["aligned_printed"]).is_file())
            save_inspection(root / "resaved.pinspect", loaded)
            loaded.storage.cleanup()

    def test_partial_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            session.status = "Cancelled"
            session.samples.append({"id": "2", "filename": "pending.png", "path": str(root / "source.png"), "state": "Cancelled", "result": None})
            path = root / "partial.pinspect"
            save_inspection(path, session)
            prior = path.read_bytes()
            with patch("src.inspection_archive.os.replace", side_effect=OSError("Disk error")):
                with self.assertRaises(OSError):
                    save_inspection(path, session)
            self.assertEqual(path.read_bytes(), prior)
            self.assertFalse(list(root.glob("*.tmp")))
            loaded = load_inspection(path)
            self.assertEqual(loaded.status, "Cancelled")
            self.assertEqual(loaded.samples[1]["state"], "Cancelled")
            self.assertIsNone(loaded.samples[1]["result"])
            loaded.storage.cleanup()

    def test_malformed_archives_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "valid.pinspect"
            save_inspection(path, synthetic_session(root))
            with zipfile.ZipFile(path) as z:
                content = {i.filename: z.read(i.filename) for i in z.infolist()}
            metadata = json.loads(content["inspection.json"])
            cases = []
            wrong_version = copy.deepcopy(metadata)
            wrong_version["schema_version"] = 999
            cases.append({**content, "inspection.json": json.dumps(wrong_version).encode()})
            missing = dict(content)
            del missing[metadata["reference"]]
            cases.append(missing)
            cases.append({**content, "../escape.png": b"x"})
            wrong_shape = copy.deepcopy(metadata)
            wrong_shape["samples"][0]["result"]["checks"] = {"ocr": "bad"}
            cases.append({**content, "inspection.json": json.dumps(wrong_shape).encode()})
            wrong_box = copy.deepcopy(metadata)
            wrong_box["samples"][0]["result"]["evidence_rows"][0]["bbox"] = [0, 0, -1, 2]
            cases.append({**content, "inspection.json": json.dumps(wrong_box).encode()})
            for index, members in enumerate(cases):
                with self.subTest(case=index):
                    bad = root / f"bad{index}.pinspect"
                    with zipfile.ZipFile(bad, "w") as z:
                        for name, payload in members.items():
                            z.writestr(name, payload)
                    with self.assertRaises(ValueError):
                        load_inspection(bad)


if __name__ == "__main__":
    unittest.main()
