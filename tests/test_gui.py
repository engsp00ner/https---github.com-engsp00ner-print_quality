"""Exercise signals, progressive results, filtering, previews and cancellation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from config import Settings
from gui.main_window import MainWindow
from src.image_loader import save_image
from src.models import OCRResult


class GUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_worker(self, window):
        deadline = time.monotonic() + 20
        while window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        if window.worker is not None:
            window.worker.requestInterruption()
            window.worker.wait(10000)
            self.fail("Worker did not finish before the timeout")

    def test_threaded_results_filter_tabs_and_responsive_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = root / "reference.png"
            image = np.full((200, 240, 3), 255, np.uint8)
            save_image(ref, image)
            window = MainWindow(replace(Settings(), output_dir=str(root), ocr_enabled=False))
            window.show()
            window.reference_path = ref
            window.set_printed_paths([ref, root / "missing.png"])
            self.assertTrue(window.start_button.isEnabled())
            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(5)
            with patch("src.ocr_engine.OCREngine.extract", return_value=OCRResult(status="SUCCESS")):
                window.start_inspection()
                self.assertFalse(window.start_button.isEnabled())
                self.wait_for_worker(window)
            timer.stop()
            self.assertTrue(ticks, "Main-thread event loop must remain responsive")
            self.assertEqual(window.table.rowCount(), 2)
            self.assertEqual(window.results[0].status, "PASS")
            self.assertEqual(window.results[1].status, "DEFECTIVE")
            window.filter.setCurrentText("DEFECTIVE")
            self.assertTrue(window.table.isRowHidden(0))
            self.assertFalse(window.table.isRowHidden(1))
            window.filter.setCurrentText("All")
            window.table.selectRow(0)
            self.app.processEvents()
            self.assertIsNotNone(window.viewers["defect_overlay"].path)
            for index, (_, key) in enumerate(window.TABS):
                window.tabs.setCurrentIndex(index)
                self.assertIsNotNone(window.viewers[key].path)
            self.assertTrue(window.export_button.isEnabled())
            self.assertTrue((window.output_folder / "batch_report.csv").exists())
            self.assertTrue(window.start_button.isEnabled())
            window.close()

    def test_cancel_preserves_completed_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i in range(8):
                path = root / f"page{i}.png"
                save_image(path, np.full((500, 500, 3), 255, np.uint8))
                paths.append(path)
            window = MainWindow(replace(Settings(), output_dir=str(root), ocr_enabled=False))
            window.reference_path = paths[0]
            window.set_printed_paths(paths)
            window.start_inspection()
            QTimer.singleShot(5, window.cancel)
            self.wait_for_worker(window)
            self.assertLess(len(window.results), len(paths))
            self.assertIn("cancelled", window.batch_label.text())
            self.assertTrue((window.output_folder / "batch_report.json").exists())
            window.close()


if __name__ == "__main__":
    unittest.main()
