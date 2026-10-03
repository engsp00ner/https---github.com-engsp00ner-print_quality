from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QFileDialog, QProgressBar
from .components import button
from .worker import PdfConversionWorker


class PdfDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("PDF to PNG")
        self.resize(640, 330)
        self.worker = None
        self.path = ""
        layout = QVBoxLayout(self)
        self.file = QLabel("Select a PDF to extract pages")
        self.file.setWordWrap(True)
        layout.addWidget(self.file)
        self.select = button("Select PDF", self.choose)
        layout.addWidget(self.select)
        row = QHBoxLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("Output directory")
        row.addWidget(self.output)
        self.browse_button = button("Browse", self.browse)
        row.addWidget(self.browse_button)
        layout.addLayout(row)
        self.dpi = QSpinBox()
        self.dpi.setRange(1, 1200)
        self.dpi.setValue(200)
        self.dpi.setPrefix("DPI: ")
        layout.addWidget(self.dpi)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("PDF password (optional)")
        layout.addWidget(self.password)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel("Each page is saved as a PNG. Existing conversion options are retained.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.convert_button = button("Extract pages", self.convert, True)
        self.convert_button.setEnabled(False)
        layout.addWidget(self.convert_button)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF (*.pdf)")
        if path:
            self.path = path
            self.file.setText(path)
            self.output.setText(str(Path(path).with_suffix("")))
            self.convert_button.setEnabled(True)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Output directory")
        if path:
            self.output.setText(path)

    def convert(self):
        if self.worker or not self.path:
            return
        self.worker = PdfConversionWorker(self.path, self.output.text() or str(Path(self.path).with_suffix("")),
                                          self.dpi.value(), self.password.text(), self)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.completed.connect(lambda folder, count: self.status.setText(f"Extracted {count} pages to {folder}"))
        self.worker.error_occurred.connect(lambda message: self.status.setText(f"Extraction failed: {message}"))
        self.worker.finished.connect(self.finished)
        for w in (self.select, self.output, self.browse_button, self.dpi, self.password, self.convert_button):
            w.setEnabled(False)
        self.progress.setRange(0, 0)
        self.worker.start()

    def update_progress(self, completed, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(completed)
        self.status.setText(f"Extracting {name}")

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        for w in (self.select, self.output, self.browse_button, self.dpi, self.password, self.convert_button):
            w.setEnabled(True)

    def reject(self):
        if self.worker:
            self.hide()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)
