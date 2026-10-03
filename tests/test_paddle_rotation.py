"""Focused workflow tests. OCR fixtures here are synthetic, never accuracy claims."""
import copy
import json
from dataclasses import replace
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from config import Settings
from gui.setup_window import MainWindow
from gui.controller import InspectionController
from gui.results_window import ResultsWindow
from src.image_loader import save_image, load_image
from src.inspection_archive import save_inspection, load_inspection
from src.models import OCRResult, OCRWord
from src.orientation import rotate_page
from src.paddle_worker import predict
from src.text_comparison import compare_text
from tests.test_archive import synthetic_session


class PaddleRotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, condition, seconds=30):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), 'GUI work timed out')

    def drain(self, controller):
        self.wait(lambda: not controller.busy and not controller.jobs and not QThreadPool.globalInstance().activeThreadCount())

    def test_filtered_recognition_uses_rec_polygons(self):
        class Result:
            json = {'res': dict(rec_texts=['actual'], rec_scores=[.9], dt_polys=[['rejected'], ['kept']], rec_polys=[['kept']])}
        class Pipeline:
            def predict(self, _):
                return [Result()]
        data = predict(Pipeline(), 'unused')
        self.assertEqual(data['regions'][0]['bbox'], ['kept'])
        self.assertEqual(data['coverage']['rejected_regions'], 1)

    def test_spatial_split_merge_and_reordered_regions(self):
        def result(rows):
            words = [OCRWord(text, 95, box, (1, 1, 1, i), granularity='line') for i, (text, box) in enumerate(rows)]
            return OCRResult('\n'.join(w.text for w in words), words=words, confidence=95,
                             status='SUCCESS', metadata={'granularity': 'line'})
        a = result([('alpha beta', (0, 0, 100, 20)), ('gamma', (0, 60, 100, 20))])
        b = result([('gamma', (0, 60, 100, 20)), ('beta', (50, 0, 50, 20)), ('alpha', (0, 0, 45, 20))])
        metrics, defects = compare_text(a, b, Settings())
        self.assertEqual(defects, [])
        self.assertEqual(metrics['text_similarity'], 1)
        b.words[0].text = 'delta'
        metrics, defects = compare_text(a, b, Settings())
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].bbox, (0, 60, 100, 20))

    def test_setup_buttons_and_exact_quarter_turns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'asymmetric.png'
            pixels = np.arange(60 * 90 * 3, dtype=np.uint8).reshape(60, 90, 3)
            save_image(path, pixels)
            window = MainWindow(Settings())
            window.set_reference(path)
            window.set_printed_paths([path])
            window.samples.setCurrentRow(0)
            self.wait(lambda: window.reference_view.image_loaded and window.sample_view.image_loaded)
            original = window.reference_view.source_preview.copy()
            for _ in range(4):
                QTest.mouseClick(window.reference_rotation_controls.right, Qt.MouseButton.LeftButton)
            self.assertEqual(window.reference_orientation, 0)
            self.assertEqual(window.reference_view.source_preview, original)
            QTest.mouseClick(window.sample_rotation_controls.left, Qt.MouseButton.LeftButton)
            self.assertEqual(window.printed_orientations[str(path.resolve())], 270)
            self.assertEqual(window.sample_view.view.sceneRect().width(), 60)
            rotated = pixels.copy()
            for _ in range(4):
                rotated = rotate_page(rotated, 90)
            np.testing.assert_array_equal(rotated, pixels)
            np.testing.assert_array_equal(load_image(path), pixels)
            self.wait(lambda: not window.samples.jobs and not QThreadPool.globalInstance().activeThreadCount())
            window.close()

    def test_stale_subset_reinspection_cache_archive_and_busy_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = synthetic_session(root)
            second = copy.deepcopy(session.samples[0])
            second.update(id='2', filename='second.png', path=str(root / 'second.png'))
            save_image(second['path'], load_image(session.reference))
            session.samples.append(second)
            session.settings = replace(Settings(), output_dir=folder, ocr_enabled=True).to_dict()
            session.reference_rotation = dict(status='MANUAL', correction_clockwise=0, method='manual')
            for sample in session.samples:
                sample['rotation'] = dict(status='CONFIDENT', correction_clockwise=180, method='osd')
            controller = InspectionController()
            view = ResultsWindow(session, controller)
            self.wait(lambda: not view.queue.jobs and view.viewer.image_loaded)
            untouched = copy.deepcopy(session.samples[1])
            QTest.mouseClick(view.rotation_controls.left, Qt.MouseButton.LeftButton)
            self.assertEqual(session.rotation(0)['correction_clockwise'], 90)
            self.assertEqual(session.samples[1], untouched)
            self.assertTrue(session.samples[0]['stale'])
            self.assertEqual(view.table.rowCount(), 0)
            self.assertEqual(view.viewer.pending_rows, [])
            self.assertEqual(view.metrics.toPlainText(), '')
            self.assertTrue(view.reinspect_selected.isEnabled())
            path = root / 'stale.pinspect'
            save_inspection(path, session)
            original_archive = path.read_bytes()
            with patch('src.ocr_engine.OCREngine.__init__', side_effect=AssertionError('OCR on reopen')):
                reopened = load_inspection(path)
            self.assertEqual(reopened.rotation(0)['status'], 'MANUAL')
            self.assertTrue(reopened.samples[0]['stale'])
            fixture = OCRResult('synthetic', words=[OCRWord('synthetic', 95, (10, 10, 50, 20), (1, 1, 1, 1))], confidence=95, status='SUCCESS')
            with patch('src.ocr_engine.OCREngine.extract', return_value=fixture) as extraction:
                self.assertTrue(controller.reinspect(reopened, 0))
                self.assertFalse(controller.rotate(reopened, 0, 90))
                self.assertFalse(controller.reinspect(reopened, 0))
                self.drain(controller)
                self.assertEqual(reopened.rotation(0)['status'], 'MANUAL')
                self.assertEqual(reopened.rotation(0)['correction_clockwise'], 90)
                self.assertFalse(reopened.samples[0]['stale'])
                self.assertEqual(path.read_bytes(), original_archive)
                first_calls = extraction.call_count
                self.assertTrue(controller.reinspect(reopened, 0))
                self.drain(controller)
                self.assertEqual(extraction.call_count, first_calls + 1, 'Reference OCR should be cached')
                self.assertTrue(controller.rotate(reopened, None, 90))
                self.assertTrue(all(s['stale'] for s in reopened.samples))
                self.assertFalse(controller.reinspect(reopened, 0))
                self.assertTrue(controller.reinspect(reopened))
                self.drain(controller)
                self.assertFalse(reopened.reference_dirty)
                self.assertFalse(any(s['stale'] for s in reopened.samples))
                self.assertEqual(reopened.rotation()['correction_clockwise'], 90)
                self.assertEqual(reopened.samples[0]['result']['orientation']['printed']['status'], 'MANUAL')
            final = load_inspection(reopened.autosave_path)
            self.assertEqual(final.rotation(0)['correction_clockwise'], 90)
            self.assertEqual(final.samples[0]['result']['ocr'], json.loads(json.dumps(reopened.samples[0]['result']['ocr'])))
            self.assertTrue(controller.rotate(reopened, 0, None))
            self.assertEqual(reopened.rotation(0)['status'], 'PENDING_AUTO')
            self.assertIsNone(reopened.rotation(0)['override'])
            self.wait(lambda: not view.queue.jobs and not QThreadPool.globalInstance().activeThreadCount())
            view.close()
            final.storage.cleanup()
            reopened.storage.cleanup()


if __name__ == '__main__':
    unittest.main()
