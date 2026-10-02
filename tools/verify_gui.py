"""Real OCR/worker/UI smoke test; saves a reviewable screenshot and summary."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from config import ROOT, load_settings
from gui.main_window import MainWindow
from src.reporting import write_json


def main():
    app = QApplication([])
    # Windows' offscreen Qt platform may not enumerate installed fonts.
    if not QFontDatabase.families():
        for filename in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
            font = Path("C:/Windows/Fonts") / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
    window = MainWindow(load_settings())
    window.show()
    window.reference_path = ROOT / "samples" / "standard.png"
    window.reference_label.setText(str(window.reference_path))
    window.set_printed_paths([ROOT / "samples" / name for name in ("printed_clean.png", "printed_defective.png", "printed_misaligned.png")])
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(50)
    window.start_inspection()
    deadline = time.monotonic() + 180
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    timer.stop()
    if window.worker is not None:
        window.worker.requestInterruption()
        window.worker.wait(100000)
        raise RuntimeError("GUI inspection exceeded its timeout")
    assert len(window.results) == 3
    assert window.results[0].status == "PASS"
    assert window.results[1].status == "DEFECTIVE"
    assert window.results[2].status == "PASS", "Rotation and shift alone must not fail this clean sample"
    assert all(r.inspection_complete for r in window.results)
    assert len(ticks) > 5
    window.table.selectRow(1)
    app.processEvents()
    window.grab().save(str(ROOT / "outputs" / "gui_smoke.png"))
    summary = {"gui_started": True, "responsive_timer_ticks": len(ticks),
               "results": [{"filename": r.filename, "status": r.status, "defects": len(r.defects)} for r in window.results],
               "output_folder": str(window.output_folder)}
    write_json(ROOT / "outputs" / "gui_smoke.json", summary)
    print(summary)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
