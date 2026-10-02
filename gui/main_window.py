from dataclasses import replace
import json
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QGroupBox, QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QComboBox, QProgressBar, QTabWidget, QPlainTextEdit, QDialog, QFormLayout,
    QLineEdit, QDoubleSpinBox, QSpinBox, QDialogButtonBox, QCheckBox)
from config import ROOT
from src.image_loader import discover_images
from src.reporting import export_csv, write_json
from .image_viewer import ImageViewer
from .worker import InspectionWorker, PdfConversionWorker
from .styles import STYLE


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspection settings")
        self.resize(660, 540)
        self.settings = settings
        self.fields = {}
        layout = QFormLayout(self)
        for name, label in (("tesseract_path", "Tesseract executable"), ("tessdata_dir", "Tessdata directory (optional)"),
                            ("ocr_languages", "OCR languages"), ("output_dir", "Output directory")):
            edit = QLineEdit(getattr(settings, name))
            layout.addRow(label, edit)
            self.fields[name] = edit
        enabled = QCheckBox("Enable content inspection (ara+eng)")
        enabled.setChecked(settings.ocr_enabled)
        layout.addRow("OCR", enabled)
        self.fields["ocr_enabled"] = enabled
        for name, label in (("ssim_threshold", "Minimum SSIM"), ("text_similarity_threshold", "Minimum text similarity"),
                            ("extra_ink_threshold", "Maximum extra ink ratio"), ("missing_ink_threshold", "Maximum missing ink ratio")):
            spin = QDoubleSpinBox()
            spin.setRange(0, 1)
            spin.setDecimals(4)
            spin.setSingleStep(.001)
            spin.setValue(getattr(settings, name))
            layout.addRow(label, spin)
            self.fields[name] = spin
        for name, label, low, high in (("min_defect_area", "Minimum defect area (pixels)", 1, 100000),
                                      ("min_blob_area", "Minimum blob area (pixels)", 1, 100000),
                                      ("min_line_length", "Minimum streak length (pixels)", 10, 10000),
                                      ("registration_tolerance", "Registration tolerance (pixels)", 0, 10)):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(settings, name))
            layout.addRow(label, spin)
            self.fields[name] = spin
        note = QLabel("Thresholds require calibration on real scans. Localized defects also fail inspection.\nDisabling OCR keeps visual analysis available, but marks content inspection incomplete.")
        note.setWordWrap(True)
        layout.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        values = {}
        for name, widget in self.fields.items():
            values[name] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.text().strip() if isinstance(widget, QLineEdit) else widget.value()
        if not values["ocr_languages"] or not values["output_dir"]:
            raise ValueError("OCR languages and output directory cannot be empty")
        return replace(self.settings, **values)


class MainWindow(QMainWindow):
    HEADERS = ["Filename", "Status", "Defects", "Text errors", "Extra ink", "Missing ink", "Streaks", "SSIM", "Text similarity", "Time (s)"]
    TABS = [("Defect Overlay", "defect_overlay"), ("Original Printed", "original_printed"),
            ("Aligned Image", "aligned_printed"), ("Difference Mask", "difference_mask"),
            ("Extra Ink", "extra_ink_mask"), ("Missing Ink", "missing_ink_mask"), ("Detected Lines", "detected_lines")]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.reference_path = None
        self.printed_paths = []
        self.results = []
        self.worker = None
        self.output_folder = None
        self.current_result = None
        self.close_pending = False
        self.pdf_path = None
        self.pdf_worker = None
        self.setWindowTitle("Print Defect Inspection System")
        self.resize(1400, 900)
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        selection = QHBoxLayout()
        reference_group = QGroupBox("REFERENCE DOCUMENT")
        reference_layout = QVBoxLayout(reference_group)
        self.reference_button = QPushButton("Select Reference Image")
        self.reference_button.clicked.connect(self.select_reference)
        self.reference_label = QLabel("Choose the correct document")
        self.reference_label.setWordWrap(True)
        reference_layout.addWidget(self.reference_button)
        reference_layout.addWidget(self.reference_label)
        printed_group = QGroupBox("PRINTED DOCUMENTS")
        printed_layout = QVBoxLayout(printed_group)
        buttons = QHBoxLayout()
        self.images_button = QPushButton("Select Images")
        self.images_button.clicked.connect(self.select_images)
        self.folder_button = QPushButton("Select Folder")
        self.folder_button.clicked.connect(self.select_folder)
        buttons.addWidget(self.images_button)
        buttons.addWidget(self.folder_button)
        printed_layout.addLayout(buttons)
        self.count_label = QLabel("Selected images: 0")
        printed_layout.addWidget(self.count_label)
        actions = QVBoxLayout()
        self.start_button = QPushButton("START INSPECTION")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_inspection)
        self.start_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel after current image")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.edit_settings)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.settings_button)
        selection.addWidget(reference_group, 2)
        selection.addWidget(printed_group, 2)
        selection.addLayout(actions, 1)
        root.addLayout(selection)

        pdf_group = QGroupBox("PDF TO PNG")
        pdf_layout = QVBoxLayout(pdf_group)
        pdf_file_row = QHBoxLayout()
        self.pdf_file_button = QPushButton("Select PDF")
        self.pdf_file_button.clicked.connect(self.select_pdf)
        self.pdf_file_label = QLabel("No PDF selected")
        self.pdf_file_label.setWordWrap(True)
        pdf_file_row.addWidget(self.pdf_file_button)
        pdf_file_row.addWidget(self.pdf_file_label, 1)
        pdf_layout.addLayout(pdf_file_row)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output directory:"))
        self.pdf_output_edit = QLineEdit()
        self.pdf_output_edit.setPlaceholderText("Defaults to a folder beside the PDF named after the PDF")
        self.pdf_output_button = QPushButton("Browse")
        self.pdf_output_button.clicked.connect(self.select_pdf_output)
        output_row.addWidget(self.pdf_output_edit, 1)
        output_row.addWidget(self.pdf_output_button)
        pdf_layout.addLayout(output_row)
        pdf_options = QHBoxLayout()
        pdf_options.addWidget(QLabel("DPI:"))
        self.pdf_dpi = QSpinBox()
        self.pdf_dpi.setRange(1, 1200)
        self.pdf_dpi.setValue(200)
        pdf_options.addWidget(self.pdf_dpi)
        pdf_options.addStretch(1)
        self.pdf_convert_button = QPushButton("Extract pages")
        self.pdf_convert_button.setEnabled(False)
        self.pdf_convert_button.clicked.connect(self.convert_pdf)
        pdf_options.addWidget(self.pdf_convert_button)
        pdf_layout.addLayout(pdf_options)
        self.pdf_status_label = QLabel("Select a PDF to extract its pages as PNG images.")
        self.pdf_status_label.setWordWrap(True)
        pdf_layout.addWidget(self.pdf_status_label)
        root.addWidget(pdf_group)
        self.progress_label = QLabel("Ready — choose reference and printed images")
        self.progress = QProgressBar()
        root.addWidget(self.progress_label)
        root.addWidget(self.progress)
        summary_bar = QHBoxLayout()
        self.summary_label = QLabel("Total: 0    PASS: 0    DEFECTIVE: 0")
        summary_bar.addWidget(self.summary_label, 1)
        summary_bar.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        self.filter.addItems(["All", "PASS", "DEFECTIVE"])
        self.filter.currentTextChanged.connect(self.apply_filter)
        summary_bar.addWidget(self.filter)
        root.addLayout(summary_bar)
        vertical = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.select_result)
        vertical.addWidget(self.table)
        previews = QSplitter(Qt.Orientation.Horizontal)
        self.reference_view = ImageViewer("Reference preview")
        previews.addWidget(self.reference_view)
        self.tabs = QTabWidget()
        self.viewers = {}
        for label, key in self.TABS:
            viewer = ImageViewer(label)
            self.viewers[key] = viewer
            self.tabs.addTab(viewer, label)
        ocr_widget = QWidget()
        ocr_layout = QVBoxLayout(ocr_widget)
        text_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.reference_text = QPlainTextEdit()
        self.printed_text = QPlainTextEdit()
        for title, edit in (("REFERENCE OCR TEXT", self.reference_text), ("PRINTED OCR TEXT", self.printed_text)):
            group = QGroupBox(title)
            layout = QVBoxLayout(group)
            edit.setReadOnly(True)
            layout.addWidget(edit)
            text_splitter.addWidget(group)
        ocr_layout.addWidget(text_splitter, 2)
        self.ocr_differences = QPlainTextEdit()
        self.ocr_differences.setReadOnly(True)
        ocr_layout.addWidget(QLabel("Detected differences and metrics"))
        ocr_layout.addWidget(self.ocr_differences, 1)
        self.tabs.addTab(ocr_widget, "OCR Comparison")
        self.tabs.currentChanged.connect(self.load_active_tab)
        previews.addWidget(self.tabs)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a result to inspect metrics, warnings and defect coordinates.")
        previews.addWidget(self.details)
        previews.setSizes([400, 700, 300])
        vertical.addWidget(previews)
        vertical.setSizes([220, 470])
        root.addWidget(vertical, 1)
        footer = QHBoxLayout()
        self.batch_label = QLabel("Reports and full-resolution overlays are saved automatically.")
        self.batch_label.setWordWrap(True)
        footer.addWidget(self.batch_label, 1)
        self.open_button = QPushButton("Open Results Folder")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_results)
        self.export_button = QPushButton("Export CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        footer.addWidget(self.open_button)
        footer.addWidget(self.export_button)
        root.addLayout(footer)

    def image_filter(self):
        return "Document images (" + " ".join("*" + ext for ext in self.settings.supported_extensions) + ")"

    def select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        self.pdf_path = Path(path).resolve()
        self.pdf_file_label.setText(str(self.pdf_path))
        self.pdf_output_edit.setText(str(self.pdf_path.with_suffix("")))
        self.pdf_convert_button.setEnabled(self.pdf_worker is None)
        self.pdf_status_label.setText("Ready to extract pages.")

    def select_pdf_output(self):
        start = self.pdf_output_edit.text().strip() or str(self.pdf_path.parent if self.pdf_path else ROOT)
        folder = QFileDialog.getExistingDirectory(self, "Select PDF output directory", start)
        if folder:
            self.pdf_output_edit.setText(folder)

    def convert_pdf(self):
        if self.pdf_worker is not None or self.pdf_path is None:
            return
        output_dir = Path(self.pdf_output_edit.text().strip() or self.pdf_path.with_suffix(""))
        self.pdf_output_edit.setText(str(output_dir))
        self.pdf_worker = PdfConversionWorker(self.pdf_path, output_dir, self.pdf_dpi.value(), parent=self)
        self.pdf_worker.completed.connect(self.on_pdf_completed)
        self.pdf_worker.error_occurred.connect(self.on_pdf_error)
        self.pdf_worker.finished.connect(self.on_pdf_worker_stopped)
        self.pdf_convert_button.setEnabled(False)
        self.pdf_file_button.setEnabled(False)
        self.pdf_output_button.setEnabled(False)
        self.pdf_dpi.setEnabled(False)
        self.pdf_status_label.setText("Extracting PDF pagesâ€¦")
        self.pdf_worker.start()

    def on_pdf_completed(self, folder, count):
        self.pdf_status_label.setText(f"Extracted {count} page(s) to {folder}")

    def on_pdf_error(self, message):
        self.pdf_status_label.setText("PDF extraction failed.")
        QMessageBox.warning(self, "PDF extraction failed", message)

    def on_pdf_worker_stopped(self):
        worker = self.pdf_worker
        self.pdf_worker = None
        worker.deleteLater()
        self.pdf_file_button.setEnabled(True)
        self.pdf_output_button.setEnabled(True)
        self.pdf_dpi.setEnabled(True)
        self.pdf_convert_button.setEnabled(self.pdf_path is not None)
        if self.close_pending and self.worker is None:
            self.close()

    def select_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select reference image", "", self.image_filter())
        if path:
            self.reference_path = Path(path)
            self.reference_label.setText(f"{self.reference_path.name}\n{self.reference_path}")
            self.reference_view.load(path)
            self.update_start()

    def select_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select printed images", "", self.image_filter())
        if paths:
            self.set_printed_paths(paths)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder of printed images")
        if folder:
            try:
                paths = discover_images(folder, self.settings)
                if not paths:
                    QMessageBox.information(self, "Empty folder", "No supported images were found in this folder.")
                self.set_printed_paths(paths)
            except OSError as exc:
                QMessageBox.warning(self, "Cannot read folder", str(exc))

    def set_printed_paths(self, paths):
        self.printed_paths = list(dict.fromkeys(Path(path).resolve() for path in paths))
        self.count_label.setText(f"Selected images: {len(self.printed_paths)}")
        self.count_label.setToolTip("\n".join(str(path) for path in self.printed_paths[:30]))
        self.update_start()

    def update_start(self):
        self.start_button.setEnabled(bool(self.reference_path and self.printed_paths and self.worker is None))

    def edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                updated = dialog.values()
                write_json(ROOT / "settings.json", updated.to_dict())
                self.settings = updated
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Settings error", str(exc))

    def start_inspection(self):
        if self.worker is not None or not self.reference_path or not self.printed_paths:
            return
        self.results = []
        self.current_result = None
        self.table.setRowCount(0)
        self.details.clear()
        self.reference_view.load(self.reference_path)
        for viewer in self.viewers.values():
            viewer.clear()
        for edit in (self.reference_text, self.printed_text, self.ocr_differences):
            edit.clear()
        self.tabs.setCurrentIndex(0)
        self.progress.setValue(0)
        self.summary_label.setText("Total: 0    PASS: 0    DEFECTIVE: 0")
        self.batch_label.setText("Inspection running…")
        self.export_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.output_folder = None
        for button in (self.start_button, self.reference_button, self.images_button, self.folder_button, self.settings_button):
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker = InspectionWorker(self.reference_path, self.printed_paths, self.settings, self)
        self.worker.progress_changed.connect(self.on_progress)
        self.worker.image_finished.connect(self.add_result)
        self.worker.output_ready.connect(self.on_output)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.batch_finished.connect(self.on_batch_finished)
        self.worker.finished.connect(self.on_worker_stopped)
        self.worker.start()

    def cancel(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("Cancellation requested — finishing the current image safely…")

    def on_progress(self, completed, total, name):
        self.progress.setValue(round(completed * 100 / max(1, total)))
        if self.worker is not None and self.worker.isInterruptionRequested():
            return
        self.progress_label.setText(f"Processed {completed} / {total}    Current file: {name}")

    def on_output(self, folder):
        self.output_folder = Path(folder)
        self.open_button.setEnabled(True)

    def on_error(self, message):
        QMessageBox.warning(self, "Inspection error", message)

    @staticmethod
    def percent(value):
        return "—" if value is None else f"{value * 100:.2f}%"

    def add_result(self, result):
        row = len(self.results)
        self.results.append(result)
        self.table.insertRow(row)
        metrics = result.metrics
        values = [result.filename, result.status + (" · review" if not result.inspection_complete else ""),
                  len(result.defects), metrics.get("text_error_count", "—"), self.percent(metrics.get("extra_ink_ratio")),
                  self.percent(metrics.get("missing_ink_ratio")), metrics.get("streak_count", "—"),
                  self.percent(metrics.get("ssim")), self.percent(metrics.get("text_similarity")), f"{result.processing_time_seconds:.2f}"]
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip("\n".join(result.decision_reasons + result.warnings) if column == 1 else result.printed_path)
            if column == 1:
                item.setForeground(QColor("#147341" if result.status == "PASS" else "#b32530"))
            self.table.setItem(row, column, item)
        passed = sum(r.status == "PASS" for r in self.results)
        incomplete = sum(not r.inspection_complete for r in self.results)
        self.summary_label.setText(f"Total: {len(self.results)}    PASS: {passed}    DEFECTIVE: {len(self.results) - passed}    Incomplete / review: {incomplete}")
        self.apply_filter()
        if self.current_result is None and not self.table.isRowHidden(row):
            self.table.selectRow(row)

    def apply_filter(self):
        for row, result in enumerate(self.results):
            self.table.setRowHidden(row, self.filter.currentText() not in ("All", result.status))
        if self.table.currentRow() >= 0 and self.table.isRowHidden(self.table.currentRow()):
            self.table.clearSelection()
            self.current_result = None
            self.details.clear()
            for viewer in self.viewers.values():
                viewer.clear()
            for edit in (self.reference_text, self.printed_text, self.ocr_differences):
                edit.clear()

    def select_result(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.current_result = self.results[rows[0].row()]
        result = self.current_result
        self.reference_view.load(result.reference_path)
        text = [result.filename, f"Status: {result.status}", f"Inspection complete: {result.inspection_complete}",
                f"Processing time: {result.processing_time_seconds:.2f}s", "", "DECISION"]
        text.extend(result.decision_reasons or ["All enabled checks passed"])
        text += ["", "WARNINGS", *(result.warnings or ["None"]), "", "ALIGNMENT", json.dumps(result.alignment, ensure_ascii=False, indent=2),
                 "", "METRICS", json.dumps(result.metrics, ensure_ascii=False, indent=2), "", "DEFECTS"]
        for index, defect in enumerate(result.defects, 1):
            text += [f"{index}. {defect.type} — x={defect.x}, y={defect.y}, width={defect.width}, height={defect.height}",
                     json.dumps(defect.details, ensure_ascii=False, indent=2)]
        self.details.setPlainText("\n".join(text))
        for key, edit in (("reference", self.reference_text), ("printed", self.printed_text)):
            ocr = result.ocr.get(key, {})
            edit.setPlainText(ocr.get("strict_text") or ocr.get("error") or "No text recognized")
        self.ocr_differences.setPlainText(json.dumps(result.ocr.get("comparison", {"status": "OCR comparison unavailable"}), ensure_ascii=False, indent=2))
        # Load only the active image to avoid allocating all full-page previews at once.
        for viewer in self.viewers.values():
            viewer.clear()
        self.load_active_tab()

    def load_active_tab(self, *_):
        if self.current_result is not None and self.tabs.currentIndex() < len(self.TABS):
            key = self.TABS[self.tabs.currentIndex()][1]
            self.viewers[key].load(self.current_result.artifacts.get(key))

    def on_batch_finished(self, summary):
        state = "Batch cancelled" if summary["cancelled"] else "Batch ended with errors" if summary["error"] else "Batch inspection completed"
        self.progress_label.setText(f"{state} — {len(self.results)} / {len(self.printed_paths)} images")
        self.batch_label.setText(f"{state} • {summary['duration']:.1f}s\n{summary['folder']}")
        self.batch_label.setToolTip(summary["error"])
        self.export_button.setEnabled(bool(self.results))

    def on_worker_stopped(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.cancel_button.setEnabled(False)
        for button in (self.reference_button, self.images_button, self.folder_button, self.settings_button):
            button.setEnabled(True)
        self.update_start()
        if self.close_pending:
            self.close()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str((self.output_folder or ROOT) / "inspection_export.csv"), "CSV (*.csv)")
        if path:
            try:
                export_csv(path, self.results)
                self.statusBar().showMessage(f"Exported {len(self.results)} results to {path}", 8000)
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def open_results(self):
        if self.output_folder and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_folder.resolve()))):
            QMessageBox.warning(self, "Cannot open results", str(self.output_folder))

    def closeEvent(self, event):
        if self.worker is not None or self.pdf_worker is not None:
            self.close_pending = True
            if self.worker is not None:
                self.cancel()
            if self.pdf_worker is not None:
                self.pdf_worker.requestInterruption()
            event.ignore()
        else:
            event.accept()
