"""Launch with python app.py. Headless diagnostics: python app.py --diagnose."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
from config import ROOT, load_settings


def main():
    parser = argparse.ArgumentParser(description="Print Defect Inspection System")
    parser.add_argument("--diagnose", action="store_true", help="Validate Tesseract and language data")
    parser.add_argument("--smoke-test", action="store_true", help="Construct GUI and exit after 1 second")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        handler = RotatingFileHandler(ROOT / "logs" / "inspection.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        logging.warning("File logging unavailable; console logging remains active")
    try:
        settings = load_settings()
    except (OSError, ValueError, TypeError) as exc:
        logging.error("Invalid settings: %s", exc)
        if not args.diagnose:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication(sys.argv)
            QMessageBox.critical(None, "Invalid settings.json", str(exc))
        return 1
    if args.diagnose:
        from src.ocr_engine import validate_tesseract
        try:
            print(json.dumps(validate_tesseract(settings), ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            logging.error("OCR validation: %s", exc)
            return 1
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("Print Defect Inspection System")
    window = MainWindow(settings)
    window.show()
    if args.smoke_test:
        QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
