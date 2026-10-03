"""Real project pixels, actual Paddle runtime, GUI interactions and screenshots."""
import copy
from dataclasses import asdict
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from config import ROOT, load_settings
from gui.setup_window import MainWindow
from src.image_loader import load_image
from src.inspection_archive import load_inspection
from src.orientation import rotate_page
from src.ocr_engine import OCREngine
from src.paddle_runtime import close_clients


def main():
    output = ROOT / 'outputs/paddle_rotation_validation'
    output.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    benchmark = ROOT / 'outputs/ocr_benchmark/batch_20261003_025033_58ce3f1b'
    source = load_image(ROOT / 'standard_image.png', settings)
    upright = rotate_page(source, 180)
    np.testing.assert_array_equal(upright, load_image(benchmark / 'shared_upright.png', settings))
    result = OCREngine(settings).extract(upright)
    assert result.status == 'SUCCESS', result.error
    recorded = json.loads((benchmark / 'paddleocr_worker.json').read_text(encoding='utf-8'))['jobs'][0]['regions']
    assert [r['text'] for r in recorded] == [w.text for w in result.words]
    assert [r['bbox'] for r in recorded] == [w.polygon for w in result.words]
    assert [r['confidence'] for r in recorded] == [w.confidence / 100 for w in result.words]
    (output / 'production_ocr.json').write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'actual_text.txt').write_text(result.strict_text, encoding='utf-8')
    print('Production matches benchmark: text, polygons and scores; 19 regions', flush=True)

    app = QApplication([])
    app.setStyle('Fusion')
    window = MainWindow(settings)
    window.resize(1440, 900)
    window.show()
    errors, ticks = [], []
    window.controller.error.disconnect()
    window.controller.error.connect(errors.append)
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(40)

    def wait(condition, seconds=240):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert condition(), 'Timed out'
        assert not errors, errors

    def idle():
        return not window.controller.busy and not window.controller.jobs

    def capture(widget, name):
        wait(lambda: not QThreadPool.globalInstance().activeThreadCount())
        app.processEvents()
        widget.grab().save(str(output / name))

    window.set_reference(ROOT / 'standard_image.png')
    window.set_printed_paths([ROOT / 'standard_image.png', ROOT / 'standard_image1.jpeg'])
    window.samples.setCurrentRow(0)
    wait(lambda: window.start_button.isEnabled() and window.sample_view.image_loaded)
    capture(window, 'setup.png')
    window.start_inspection()
    assert not window.live.reference_rotation_controls.isEnabled()
    assert not window.live.sample_rotation_controls.isEnabled()
    window.live.resize(1440, 900)
    capture(window.live, 'live_locked.png')
    wait(idle)
    session = window.controller.session
    assert all(s.get('result') for s in session.samples)
    assert all(s['result']['ocr']['printed']['status'] == 'SUCCESS' for s in session.samples)
    view = window.result_windows[-1]
    view.resize(1440, 900)
    view.queue.setCurrentRow(0)
    capture(view, 'results.png')
    unaffected = copy.deepcopy(session.samples[1])
    base_angle = session.rotation(0)['correction_clockwise']
    QTest.mouseClick(view.rotation_controls.left, Qt.MouseButton.LeftButton)
    assert session.rotation(0)['correction_clockwise'] == (base_angle - 90) % 360
    capture(view, 'sample_stale.png')
    QTest.mouseClick(view.rotation_controls.right, Qt.MouseButton.LeftButton)
    assert session.samples[1] == unaffected
    QTest.mouseClick(view.reinspect_selected, Qt.MouseButton.LeftButton)
    wait(idle)
    assert session.samples[1] == unaffected
    assert session.rotation(0)['status'] == 'MANUAL'
    assert session.rotation(0)['correction_clockwise'] == base_angle
    assert not session.samples[0]['stale']
    assert view.table.rowCount() == len(session.samples[0]['result']['evidence_rows'])
    capture(view, 'sample_reinspected.png')
    print('Sample reinspection preserved unaffected result and manual override', flush=True)
    view.view_reference()
    QTest.mouseClick(view.rotation_controls.right, Qt.MouseButton.LeftButton)
    assert all(s['stale'] for s in session.samples)
    assert not view.reinspect_selected.isEnabled()
    capture(view, 'reference_stale.png')
    QTest.mouseClick(view.rotation_controls.left, Qt.MouseButton.LeftButton)
    QTest.mouseClick(view.reinspect_all, Qt.MouseButton.LeftButton)
    wait(idle)
    assert not any(s['stale'] for s in session.samples)
    assert not session.reference_dirty
    capture(view, 'all_reinspected.png')
    archive = Path(session.autosave_path)
    saved_bytes = archive.read_bytes()
    with patch('src.ocr_engine.OCREngine.__init__', side_effect=AssertionError('OCR initialized while opening')):
        opened = load_inspection(archive)
        window.show_results(opened)
    assert opened.rotation()['status'] == 'MANUAL'
    assert opened.rotation(0)['status'] == 'MANUAL'
    assert window.controller.reinspect(opened, 0)
    wait(idle)
    assert archive.read_bytes() == saved_bytes
    assert opened.autosave_path != str(archive)
    assert opened.samples[0]['result']['ocr']['printed']['status'] == 'SUCCESS'
    assert opened.rotation(0)['correction_clockwise'] == base_angle
    capture(window.result_windows[-1], 'saved_run_reinspected.png')
    summary = dict(benchmark_match=True, equivalent_pixels=True, regions=len(result.words),
                   gui_timer_ticks=len(ticks), errors=errors, actual_archive=str(archive),
                   reopened_autosave=opened.autosave_path,
                   sample_states=[s['state'] for s in session.samples],
                   checks=[s['result']['checks'] for s in session.samples],
                   sample_rotations=[session.rotation(i) for i in range(len(session.samples))],
                   reference_rotation=session.rotation())
    (output / 'validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    wait(lambda: idle() and not any(w.queue.jobs for w in window.result_windows) and not window.live.queue.jobs
         and not window.samples.jobs and not QThreadPool.globalInstance().activeThreadCount())
    window.close()
    close_clients()
    print('Actual OCR / rotation / saved reinspection workflow completed', flush=True)


if __name__ == '__main__':
    main()
