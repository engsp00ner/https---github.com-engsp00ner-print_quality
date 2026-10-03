from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QThreadPool
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QFileDialog, QMessageBox, QDialog
from config import ROOT
from src.image_loader import discover_images
from src.reporting import write_json
from .components import DesktopWindow, InspectionViewer, SampleList, panel, button, RotationControls
from .controller import InspectionController
from .dialogs import SettingsDialog, OrientationDialog
from .pdf_dialog import PdfDialog
from .live_window import LiveWindow
from .results_window import ResultsWindow


class MainWindow(DesktopWindow):
    def __init__(self, settings):
        super().__init__("setup", "Print Inspection", "Prepare a new inspection")
        self.settings = settings
        self.reference_path = None
        self.printed_paths = []
        self.reference_orientation = None
        self.printed_orientations = {}
        self.controller = InspectionController(self)
        self.live = None
        self.result_windows = []
        self.pdf = PdfDialog(self)
        self.header.addWidget(button("Open saved inspection", self.open_saved))
        self.settings_button = button("Settings", self.edit_settings)
        self.header.addWidget(self.settings_button)
        self.header.addWidget(button("PDF to PNG", self.pdf.show))
        self.body = QSplitter()
        reference_panel, reference = panel("①  Reference document", "Select the standard document to compare against.")
        self.reference_view = InspectionViewer("Choose a reference image")
        self.reference_view.loaded.connect(self.update_start)
        reference.addWidget(self.reference_view, 1)
        self.reference_rotation_controls = RotationControls()
        self.reference_rotation_controls.changed.connect(self.rotate_reference)
        reference.addWidget(self.reference_rotation_controls)
        self.reference_label = QLabel("No reference selected")
        self.reference_label.setWordWrap(True)
        self.reference_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reference.addWidget(self.reference_label)
        reference_buttons = QHBoxLayout()
        self.reference_button = button("Change reference", self.select_reference)
        self.remove_reference_button = button("Remove", self.remove_reference)
        reference_buttons.addWidget(self.reference_button)
        reference_buttons.addWidget(self.remove_reference_button)
        reference.addLayout(reference_buttons)
        self.body.addWidget(reference_panel)
        samples_panel, samples = panel("②  Printed samples", "Add printed sample images to be inspected against the reference.")
        controls = QHBoxLayout()
        self.images_button = button("Add images", self.select_images, True)
        self.folder_button = button("Add folder", self.select_folder)
        controls.addWidget(self.images_button)
        controls.addWidget(self.folder_button)
        self.count_label = QLabel("0 samples selected")
        controls.addWidget(self.count_label, 1)
        samples.addLayout(controls)
        self.samples = SampleList()
        self.samples.validated.connect(self.update_start)
        samples.addWidget(self.samples, 1)
        self.sample_view = InspectionViewer('Selected sample')
        samples.addWidget(self.sample_view, 1)
        self.sample_rotation_controls = RotationControls()
        self.sample_rotation_controls.changed.connect(self.rotate_sample)
        samples.addWidget(self.sample_rotation_controls)
        self.samples.currentRowChanged.connect(self.preview_sample)
        sample_actions = QHBoxLayout()
        self.remove_button = button("Remove selected", self.remove_sample)
        self.orientation_button = button("Text direction…", self.edit_orientation)
        sample_actions.addWidget(self.remove_button)
        sample_actions.addWidget(self.orientation_button)
        samples.addLayout(sample_actions)
        self.body.addWidget(samples_panel)
        self.root.addWidget(self.body, 1)
        self.body.setSizes([600, 650])
        footer_panel, footer_layout = panel("Ready for inspection")
        footer = QHBoxLayout()
        self.ready_label = QLabel("Select a reference and at least one sample")
        self.ready_label.setWordWrap(True)
        footer.addWidget(self.ready_label, 1)
        self.active_button = button("Return to live inspection", self.show_live)
        self.active_button.hide()
        footer.addWidget(self.active_button)
        self.start_button = button("Start inspection  →", self.start_inspection, True)
        self.start_button.setMinimumHeight(48)
        footer.addWidget(self.start_button)
        footer_layout.addLayout(footer)
        self.root.addWidget(footer_panel)
        self.controller.changed.connect(self.update_start)
        self.controller.completed.connect(self.show_results)
        self.controller.opened.connect(self.show_results)
        self.controller.error.connect(self.show_error)
        self.restore_splitters()
        self.update_start()

    @property
    def worker(self):
        return self.controller.worker

    def image_filter(self):
        return "Document images (" + " ".join("*" + ext for ext in self.settings.supported_extensions) + ")"

    def select_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select reference", "", self.image_filter())
        if path:
            self.set_reference(path)

    def set_reference(self, path):
        self.reference_path = Path(path).resolve() if path else None
        self.reference_orientation = None
        self.reference_view.load(self.reference_path)
        if self.reference_path and self.reference_path.is_file():
            size = QImageReader(str(self.reference_path)).size()
            self.reference_label.setText(f"{self.reference_path.name}\n{size.width()} × {size.height()} · {self.reference_path.stat().st_size // 1024} KB")
        else:
            self.reference_label.setText("No reference selected")
        self.update_start()

    def remove_reference(self):
        self.set_reference(None)

    def select_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add printed samples", "", self.image_filter())
        self.set_printed_paths(self.printed_paths + paths)

    def select_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Add sample folder")
        if path:
            try:
                self.set_printed_paths(self.printed_paths + discover_images(path, self.settings))
            except OSError as exc:
                self.show_error(str(exc))

    def set_printed_paths(self, paths):
        self.printed_paths = list(dict.fromkeys(Path(p).resolve() for p in paths))
        self.printed_orientations = {str(p): self.printed_orientations[str(p)] for p in self.printed_paths if str(p) in self.printed_orientations}
        self.samples.populate([{"filename": p.name, "path": str(p), "state": "Ready" if p.is_file() else "Failed"} for p in self.printed_paths])
        self.count_label.setText(f"{len(self.printed_paths)} samples selected")
        self.update_start()

    def remove_sample(self):
        row = self.samples.currentRow()
        if row >= 0:
            self.set_printed_paths([p for i, p in enumerate(self.printed_paths) if i != row])

    def valid_inputs(self):
        return bool(self.reference_path and self.reference_path.is_file() and self.reference_view.path and
                    self.reference_view.image_loaded and self.printed_paths and
                    all(p.is_file() and str(p) in self.samples.valid_paths for p in self.printed_paths))

    def update_start(self):
        busy = self.controller.busy
        self.start_button.setEnabled(self.valid_inputs() and not busy)
        self.reference_rotation_controls.setEnabled(not busy and bool(self.reference_path))
        self.sample_rotation_controls.setEnabled(not busy and self.samples.currentRow() >= 0)
        for w in (self.reference_button, self.remove_reference_button, self.images_button, self.folder_button, self.remove_button, self.orientation_button, self.settings_button):
            w.setEnabled(not busy)
        self.active_button.setVisible(self.live is not None)
        self.ready_label.setText("Inspection running · inputs locked" if busy else f"✓ Reference ready  ·  {len(self.printed_paths)} samples" if self.valid_inputs() else "Select an existing reference and sample images")

    def edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                settings = dialog.values()
                write_json(ROOT / "settings.json", settings.to_dict())
                self.settings = settings
            except (ValueError, OSError) as exc:
                self.show_error(str(exc))

    def edit_orientation(self):
        dialog = OrientationDialog(self.reference_path, self.printed_paths, self.reference_orientation, self.printed_orientations, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reference_orientation, self.printed_orientations = dialog.values()
            self.reference_view.load_source(self.reference_path, self.reference_orientation or 0)
            self.preview_sample(self.samples.currentRow())

    def rotate_reference(self, delta):
        if self.controller.busy or not self.reference_path:
            return
        self.reference_orientation = None if delta is None else ((self.reference_orientation or 0) + delta) % 360
        self.reference_view.load_source(self.reference_path, self.reference_orientation or 0)

    def rotate_sample(self, delta):
        row = self.samples.currentRow()
        if self.controller.busy or row < 0:
            return
        key = str(self.printed_paths[row])
        self.printed_orientations[key] = None if delta is None else ((self.printed_orientations.get(key) or 0) + delta) % 360
        self.preview_sample(row)

    def preview_sample(self, row):
        if 0 <= row < len(self.printed_paths):
            path = self.printed_paths[row]
            self.sample_view.load_source(path, self.printed_orientations.get(str(path)) or 0)
        else:
            self.sample_view.clear()
        self.update_start()

    def start_inspection(self):
        if self.controller.busy or not self.valid_inputs():
            return
        # Queued worker signals are delivered after the live window has subscribed.
        if self.controller.start(self.reference_path, self.printed_paths, self.settings, self.reference_orientation, self.printed_orientations):
            if self.live:
                self.live.hide()
            self.live = LiveWindow(self.controller)
            self.show_live()
            self.update_start()

    def show_live(self):
        if self.live:
            self.live.show()
            self.live.raise_()
            self.live.activateWindow()

    def cancel(self):
        self.controller.cancel()

    def show_results(self, session):
        for window in self.result_windows:
            if window.session is session:
                window.refresh()
                window.show()
                return
        window = ResultsWindow(session, self.controller)
        self.result_windows.append(window)
        window.show()

    def open_saved(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open saved inspection", "", "Portable inspection (*.pinspect)")
        if path:
            self.controller.open(path)

    def show_error(self, message):
        for window in self.result_windows:
            window.save_status.setText("Operation failed · results retained; Save inspection to retry")
        QMessageBox.warning(self, "Inspection operation failed", message)

    def closeEvent(self, event):
        pending_thumbnails = bool(self.samples.jobs) or any(w.queue.jobs for w in self.result_windows) or bool(self.live and self.live.queue.jobs)
        if self.controller.busy or self.controller.jobs or self.pdf.worker or pending_thumbnails or QThreadPool.globalInstance().activeThreadCount():
            self.statusBar().showMessage("Work is still running. Use Cancel after current sample in Live Inspection, then close when saving finishes.")
            event.ignore()
            return
        for window in self.result_windows:
            window.close()
        if self.live:
            self.live.hide()
        self.pdf.close()
        super().closeEvent(event)
