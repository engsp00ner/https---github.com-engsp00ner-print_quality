from dataclasses import replace
import json
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QGroupBox, QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QComboBox, QProgressBar, QTabWidget, QPlainTextEdit, QDialog, QFormLayout,
    QLineEdit, QDoubleSpinBox, QSpinBox, QDialogButtonBox, QCheckBox, QScrollArea)
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
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QFormLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        available = self.screen().availableGeometry()
        self.resize(min(660, available.width()), min(540, available.height() - 50))
        for name, label in (("paddle_python", "PaddleOCR Python executable"), ("paddle_model_dir", "PaddleOCR local models"),
                            ("tesseract_path", "Tesseract (orientation only)"), ("tessdata_dir", "Orientation tessdata directory"),
                            ("ocr_languages", "Orientation probe languages"), ("output_dir", "Output directory")):
            edit = QLineEdit(getattr(settings, name))
            layout.addRow(label, edit)
            self.fields[name] = edit
        enabled = QCheckBox("Enable PaddleOCR Arabic content inspection")
        enabled.setChecked(settings.ocr_enabled)
        layout.addRow("OCR", enabled)
        self.fields["ocr_enabled"] = enabled
        orientation = QCheckBox("Detect text direction automatically")
        orientation.setChecked(settings.auto_orientation)
        layout.addRow("Text direction", orientation)
        self.fields["auto_orientation"] = orientation
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
        note = QLabel("Thresholds require calibration on real scans. Localized defects also fail inspection.\nOCR is optional. When disabled, PASS covers visual checks only; text recognition is skipped.")
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


class OrientationDialog(QDialog):
    """Per-page choices are clockwise corrections after EXIF orientation."""
    def __init__(self, reference, printed_paths, reference_angle, printed_angles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text direction")
        self.resize(660, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose clockwise corrections. Auto detects the page's text direction.\n"
                                "These corrections apply to the next inspection run."))
        table = QTableWidget(1 + len(printed_paths), 2)
        table.setHorizontalHeaderLabels(["Page", "Clockwise correction"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.choices = []
        rows = [(None, f"Reference: {reference.name if reference else 'not selected'}", reference_angle)]
        rows += [(str(path.resolve()), path.name, printed_angles.get(str(path.resolve()))) for path in printed_paths]
        for row, (path, label, angle) in enumerate(rows):
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(path or str(reference or ""))
            table.setItem(row, 0, item)
            combo = QComboBox()
            combo.addItem("Auto", None)
            for value in (0, 90, 180, 270):
                combo.addItem(f"{value} degrees clockwise", value)
            combo.setCurrentIndex((0, 90, 180, 270).index(angle) + 1 if angle is not None else 0)
            table.setCellWidget(row, 1, combo)
            self.choices.append((path, combo))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.choices[0][1].currentData(), {path: combo.currentData() for path, combo in self.choices[1:]
                                               if combo.currentData() is not None}
