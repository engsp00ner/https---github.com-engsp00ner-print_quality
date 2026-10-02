import logging
import time
from PySide6.QtCore import QThread, Signal
from src.inspection_engine import InspectionEngine
from src.reporting import create_batch_dir, write_batch_report

log = logging.getLogger(__name__)


class PdfConversionWorker(QThread):
    completed = Signal(str, int)
    error_occurred = Signal(str)

    def __init__(self, pdf_path, output_dir, dpi=200, password="", parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.dpi = dpi
        self.password = password

    def run(self):
        try:
            from pdf_to_images import convert as convert_pdf
            folder = convert_pdf(self.pdf_path, self.dpi, self.password, self.output_dir)
            self.completed.emit(str(folder), len(list(folder.glob("page_*.png"))))
        except Exception as exc:
            log.exception("PDF conversion failed")
            self.error_occurred.emit(str(exc))


class InspectionWorker(QThread):
    progress_changed = Signal(int, int, str)
    image_finished = Signal(object)
    batch_finished = Signal(object)
    error_occurred = Signal(str)
    output_ready = Signal(str)

    def __init__(self, reference, printed_paths, settings, parent=None):
        super().__init__(parent)
        self.reference, self.printed_paths, self.settings = reference, list(printed_paths), settings

    def run(self):
        started = time.perf_counter()
        results = []
        folder = None
        error = ""
        try:
            folder = create_batch_dir(self.settings.output_dir)
            self.output_ready.emit(str(folder.resolve()))
            self.progress_changed.emit(0, len(self.printed_paths), "Preparing reference and OCR…")
            engine = InspectionEngine(self.settings)
            engine.prepare_reference(self.reference)
            for index, path in enumerate(self.printed_paths):
                if self.isInterruptionRequested():
                    break
                self.progress_changed.emit(index, len(self.printed_paths), path.name)
                result = engine.inspect(self.reference, path, folder)
                results.append(result)
                self.image_finished.emit(result)
                self.progress_changed.emit(index + 1, len(self.printed_paths), path.name)
        except Exception as exc:
            log.exception("Batch failed")
            error = str(exc)
            self.error_occurred.emit(error)
        finally:
            duration = time.perf_counter() - started
            cancelled = self.isInterruptionRequested()
            if folder is not None:
                try:
                    write_batch_report(folder, results, self.settings, duration, cancelled)
                except Exception as exc:
                    log.exception("Batch report write failed")
                    error = f"Batch report write failed: {exc}"
                    self.error_occurred.emit(error)
            self.batch_finished.emit({"results": results, "duration": duration, "cancelled": cancelled,
                                      "folder": str(folder) if folder else "", "error": error})
