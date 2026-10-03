import logging
import time
import shutil
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from src.inspection_engine import InspectionEngine
from src.reporting import create_batch_dir, write_batch_report

log = logging.getLogger(__name__)


class PdfConversionWorker(QThread):
    completed = Signal(str, int)
    progress_changed = Signal(int, int, str)
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
            folder = convert_pdf(
                self.pdf_path, self.dpi, self.password, self.output_dir,
                progress_callback=lambda completed, total, page: self.progress_changed.emit(
                    completed, total, f"page_{page:04d}.png"
                ),
            )
            self.completed.emit(str(folder), len(list(folder.glob("page_*.png"))))
        except Exception as exc:
            log.exception("PDF conversion failed")
            self.error_occurred.emit(str(exc))


class InspectionWorker(QThread):
    sample_started = Signal(int, str)
    stage_changed = Signal(object)
    inputs_saved = Signal(object)
    progress_changed = Signal(int, int, str)
    image_finished = Signal(object)
    batch_finished = Signal(object)
    error_occurred = Signal(str)
    output_ready = Signal(str)

    def __init__(self, reference, printed_paths, settings, parent=None,
                 reference_orientation=None, printed_orientations=None):
        super().__init__(parent)
        self.reference, self.printed_paths, self.settings = reference, list(printed_paths), settings
        self.reference_orientation = reference_orientation
        self.printed_orientations = dict(printed_orientations or {})
        self.engine = None
        self.reinspection = False

    def run(self):
        started = time.perf_counter()
        results = []
        folder = None
        error = ""
        try:
            folder = create_batch_dir(self.settings.output_dir)
            self.output_ready.emit(str(folder.resolve()))
            self.stage_changed.emit({"stage": "Preserving input images", "status": "RUNNING"})
            inputs = folder / "inputs"
            inputs.mkdir()
            snapshots = {}
            for index, path in enumerate([] if self.reinspection else dict.fromkeys([Path(self.reference), *self.printed_paths])):
                if path.is_file():
                    target = inputs / f"{index:05d}{path.suffix.lower()}"
                    shutil.copyfile(path, target)
                    snapshots[str(path.resolve())] = str(target.resolve())
            self.inputs_saved.emit(snapshots)
            self.progress_changed.emit(0, len(self.printed_paths), "Preparing reference and OCR…")
            engine = self.engine or InspectionEngine(self.settings, self.reference_orientation, self.printed_orientations,
                                                     progress_callback=self.stage_changed.emit)
            self.engine = engine
            engine.progress_callback = self.stage_changed.emit
            engine.reference_orientation_override = self.reference_orientation
            engine.printed_orientation_overrides = {str(Path(p).resolve()): a for p, a in self.printed_orientations.items()}
            engine.prepare_reference(self.reference)
            for index, path in enumerate(self.printed_paths):
                if self.isInterruptionRequested():
                    break
                self.progress_changed.emit(index, len(self.printed_paths), path.name)
                self.sample_started.emit(index, str(path))
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
