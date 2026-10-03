"""Exercise the real desktop workflow and capture its three windows.

samples/ contains synthetic project fixtures; these are workflow checks, not an
accuracy evaluation. Use --layout-only for additional DPI capture from a saved run.
"""
import argparse
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QThreadPool
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from config import ROOT, load_settings
from gui.main_window import MainWindow
from src.inspection_archive import load_inspection
from src.reporting import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout-only")
    parser.add_argument("--project-images", action="store_true")
    parser.add_argument("--output-dir", default="desktop_validation")
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    if not QFontDatabase.families():
        for name in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    window = MainWindow(load_settings())
    window.show_error = lambda message: print("UI ERROR:", message, flush=True)
    window.controller.error.disconnect()
    window.controller.error.connect(window.show_error)
    output = ROOT / "outputs" / args.output_dir
    output.mkdir(exist_ok=True)
    window.set_reference(ROOT / "standard_image1.jpeg" if args.project_images else ROOT / "samples" / "standard.png")
    window.set_printed_paths([ROOT / "standard_image.png", ROOT / "standard_image1.jpeg"] if args.project_images else
                             [ROOT / "samples" / name for name in ("printed_clean.png", "printed_defective.png", "printed_misaligned.png")])
    window.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(.01)
    deadline = time.monotonic() + 10
    while not window.start_button.isEnabled() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.start_button.isEnabled()
    window.resize(1366, 720)
    QTest.qWait(100)
    window.grab().save(str(output / "setup.png"))
    ticks, stages = [], []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(40)
    if args.layout_only:
        window.show_results(load_inspection(args.layout_only))
    else:
        captured = []
        def stage(event):
            stages.append(event)
            if window.live and window.live.sample.timer.isActive() and not captured:
                window.live.resize(1366, 720)
                window.live.grab().save(str(output / "live.png"))
                captured.append(True)
        window.controller.stage.connect(stage)
        window.start_inspection()
        deadline = time.monotonic() + 300
        while (window.worker or window.controller.jobs) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.02)
        if window.worker:
            window.cancel()
            window.worker.wait()
            raise RuntimeError("Inspection exceeded timeout")
        assert len(window.result_windows) == 1
    result = window.result_windows[-1]
    result.queue.setCurrentRow(1)
    result.table.selectRow(0)
    for width, height in ((1366, 720), (1920, 1030), (1093, 570), (910, 470)):
        for name, target in (("setup", window), ("results", result), ("live", window.live)):
            if target is None:
                continue
            target.resize(width, height)
            app.processEvents()
            time.sleep(.05)
            app.processEvents()
            target.grab().save(str(output / f"{name}_{width}.png"))
    result.resize(1366, 720)
    for _ in range(15):
        app.processEvents()
        time.sleep(.01)
    result.grab().save(str(output / "results.png"))
    timer.stop()
    report = {"fixture": "Actual project Arabic forms; real engine execution" if args.project_images else "Synthetic project samples; real engine execution", "timer_ticks": len(ticks),
              "stage_events": stages, "archive": result.session.archive_path,
              "samples": [{"filename": s["filename"], "state": s["state"]} for s in result.session.samples]}
    write_json(output / "validation.json", report)
    print({k: v for k, v in report.items() if k != "stage_events"}, flush=True)
    deadline = time.monotonic() + 5
    while any(w.queue.jobs for w in window.result_windows) or window.samples.jobs or (window.live and window.live.queue.jobs) or QThreadPool.globalInstance().activeThreadCount():
        app.processEvents()
        time.sleep(.02)
        if time.monotonic() > deadline:
            print("Pending previews:", [(j.isRunning(), j.isFinished()) for j in window.samples.jobs],
                  [(j.isRunning(), j.isFinished()) for j in window.live.queue.jobs] if window.live else [],
                  QThreadPool.globalInstance().activeThreadCount(), flush=True)
            deadline = time.monotonic() + 5
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
