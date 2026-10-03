"""Focused three-window workflow tests with explicitly synthetic OCR fixtures."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer, QThreadPool
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from config import Settings
from gui.main_window import MainWindow, OrientationDialog
from gui.controller import InspectionController
from gui.results_window import ResultsWindow
from src.models import OCRResult, OCRWord
from src.inspection_session import result_state
from src.inspection_archive import load_inspection
from tests.test_archive import synthetic_session


class GUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def wait(self, condition, seconds=30):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), "Timed out waiting for GUI work")

    def drain(self, window):
        self.wait(lambda: not window.controller.busy and not window.controller.jobs and not window.samples.jobs and
                  not any(w.queue.jobs for w in window.result_windows) and not (window.live and window.live.queue.jobs) and
                  not QThreadPool.globalInstance().activeThreadCount())

    @staticmethod
    def ocr():
        return OCRResult("clean", "clean", [OCRWord("clean", 90, (10, 10, 30, 20), (1, 1, 1, 1))], 90, "SUCCESS")

    def test_selection_dedup_validation_orientation_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            path = Path(session.reference)
            window = MainWindow(Settings())
            window.show()
            window.set_reference(path)
            window.set_printed_paths([path, path])
            self.wait(lambda: window.start_button.isEnabled())
            self.assertEqual(len(window.printed_paths), 1)
            self.assertFalse(window.samples.item(0).icon().isNull())
            dialog = OrientationDialog(path, [path], None, {}, window)
            dialog.choices[0][1].setCurrentIndex(2)
            dialog.choices[1][1].setCurrentIndex(3)
            self.assertEqual(dialog.values(), (90, {str(path.resolve()): 180}))
            window.samples.setCurrentRow(0)
            window.remove_sample()
            self.assertFalse(window.start_button.isEnabled())
            window.set_printed_paths([root / "missing.png"])
            self.drain(window)
            self.assertFalse(window.start_button.isEnabled())
            window.remove_reference()
            self.assertIsNone(window.reference_path)
            window.close()

    def test_threaded_real_engine_stages_no_concurrent_runs_and_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            path = Path(session.reference)
            window = MainWindow(replace(Settings(), output_dir=str(root), ocr_enabled=False))
            window.show()
            window.set_reference(path)
            window.set_printed_paths([path])
            self.wait(lambda: window.start_button.isEnabled())
            window.reference_orientation = 0
            window.printed_orientations = {str(path): 0}
            stages, ticks = [], []
            window.controller.stage.connect(stages.append)
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(5)
            with patch("src.ocr_engine.OCREngine.extract", return_value=self.ocr()):
                window.start_inspection()
                worker = window.worker
                window.start_inspection()
                self.assertIs(worker, window.worker)
                self.assertFalse(window.start_button.isEnabled())
                window.live.close()
                self.assertFalse(window.live.isVisible())
                self.assertTrue(window.controller.busy)
                window.show_live()
                self.drain(window)
            timer.stop()
            self.assertTrue(ticks)
            result = window.controller.session
            self.assertEqual(result.samples[0]["state"], "Passed")
            self.assertTrue(Path(result.archive_path).is_file())
            self.assertTrue((Path(result.folder) / "batch_report.csv").is_file())
            self.assertTrue(any(e["stage"] == "ink" and e["status"] == "RUNNING" for e in stages))
            self.assertFalse(window.live.sample.timer.isActive())
            self.assertEqual(len(window.result_windows), 1)
            self.assertIn("No defects detected", window.result_windows[0].findings_label.text())
            window.close()

    def test_markers_table_frames_and_scan_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            session = synthetic_session(Path(directory))
            controller = InspectionController()
            window = ResultsWindow(session, controller)
            window.resize(1366, 768)
            window.show()
            self.wait(lambda: bool(window.viewer.markers))
            marker = window.viewer.markers["1"]
            self.assertEqual(marker.rect().x() * window.viewer.preview_scale, 130)
            window.table.selectRow(0)
            self.assertTrue(marker.isSelected())
            window.table.clearSelection()
            point = window.viewer.view.mapFromScene(marker.rect().topLeft())
            QTest.mouseClick(window.viewer.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(window.table.currentRow(), 0)
            window.viewer.view.zoom(1.25)
            window.resize(1200, 740)
            self.assertEqual(marker.rect().x() * window.viewer.preview_scale, 130)
            window.viewer.start_scan()
            window.viewer.animate()
            self.assertTrue(window.viewer.view.sceneRect().contains(window.viewer.beam.rect()))
            window.viewer.stop_scan()
            window.view_choice.setCurrentIndex(1)
            self.wait(lambda: not QThreadPool.globalInstance().activeThreadCount())
            self.app.processEvents()
            self.assertFalse(window.viewer.markers)
            window.filter.setCurrentText("Passed")
            self.assertEqual(window.table.rowCount(), 0)
            self.assertIsNone(window.viewer.path)
            self.wait(lambda: not window.queue.jobs)
            window.close()

    def test_cancel_partial_and_failed_review_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = synthetic_session(root)
            path = Path(session.reference)
            controller = InspectionController()
            # The controller accepts repeated paths here only to exercise queued cancellation.
            controller.started_sample.connect(lambda _: controller.cancel())
            controller.start(path, [path, path], replace(Settings(), output_dir=str(root), ocr_enabled=False), 0, {str(path): 0})
            self.wait(lambda: not controller.busy and not controller.jobs)
            self.assertEqual(controller.session.status, "Cancelled")
            self.assertEqual(controller.session.samples[0]["state"], "Review required")
            self.assertEqual(controller.session.samples[1]["state"], "Cancelled")
            loaded = load_inspection(controller.session.archive_path)
            self.assertEqual(loaded.samples[1]["state"], "Cancelled")
            loaded.storage.cleanup()
            self.assertEqual(result_state({"decision_reasons": ["Processing error; manual review required"]}), "Failed")
            self.assertEqual(result_state({"status": "PASS", "inspection_complete": False}), "Review required")


if __name__ == "__main__":
    unittest.main()
