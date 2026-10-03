import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox, QPlainTextEdit,
    QTabWidget, QFileDialog, QMessageBox)
from src.models import InspectionResult, Defect
from src.reporting import export_csv, write_json
from .components import DesktopWindow, InspectionViewer, SampleList, panel, button, RotationControls

VIEWS = [("Aligned sample · markers", "aligned_printed"), ("Original sample", "original_printed"),
         ("Upright sample", "normalized_printed"), ("Reference", "normalized_reference"),
         ("Difference mask", "difference_mask"), ("Extra ink", "extra_ink_mask"),
         ("Missing ink", "missing_ink_mask"), ("Detected lines", "detected_lines"),
         ("Saved defect overlay", "defect_overlay"), ("Valid comparison mask", "valid_comparison_mask")]
CANONICAL = {"aligned_printed", "normalized_reference", "difference_mask", "extra_ink_mask", "missing_ink_mask", "detected_lines", "valid_comparison_mask"}


def model(data):
    fields = InspectionResult.__dataclass_fields__
    kwargs = {k: v for k, v in data.items() if k in fields and k != "defects"}
    kwargs["defects"] = [Defect(**{k: v for k, v in d.items() if k in Defect.__dataclass_fields__}) for d in data["defects"]]
    return InspectionResult(**kwargs)


class ResultsWindow(DesktopWindow):
    def __init__(self, session, controller):
        super().__init__("results", "Inspection results", f"{session.status}  ·  {session.created_at[:10]}  ·  {len(session.samples)} samples")
        self.session, self.controller = session, controller
        self.current = None
        self.rows = []
        self.header.addWidget(button("Open saved inspection", self.open_saved))
        self.header.addWidget(button("Save inspection", self.save_as, True))
        summary_panel, summary = panel("Batch summary")
        summary.itemAt(0).widget().hide()
        bar = QHBoxLayout()
        counts = {state: sum(s["state"] == state for s in session.samples) for state in ("Passed", "Defective", "Review required", "Failed", "Cancelled")}
        self.summary_label = QLabel(f"<b>{len(session.samples)}</b> Total &nbsp;&nbsp; " + " &nbsp;&nbsp; ".join(f"<span style='color:{color}'><b>{counts[state]}</b> {state}</span>" for state, color in (("Passed", "#13843b"), ("Defective", "#dc2638"), ("Review required", "#ad7400"), ("Failed", "#dc2638"), ("Cancelled", "#718096"))))
        self.summary_label.setWordWrap(True)
        bar.addWidget(self.summary_label, 1)
        self.filter = QComboBox()
        self.filter.addItems(["All samples", "Passed", "Defective", "Review required", "Failed / incomplete"])
        self.filter.currentTextChanged.connect(self.apply_filter)
        bar.addWidget(self.filter)
        summary.addLayout(bar)
        self.root.addWidget(summary_panel)
        self.body = QSplitter()
        self.queue_panel, queue_layout = panel("Sample queue")
        self.queue = SampleList()
        self.queue.populate(session.samples)
        self.queue.currentRowChanged.connect(self.select_sample)
        queue_layout.addWidget(self.queue)
        self.body.addWidget(self.queue_panel)
        self.splitter = QSplitter()
        self.secondary_tabs = QTabWidget()
        self.secondary_tabs.hide()
        self.narrow = False
        viewer_panel, viewer_layout = panel("Selected sample")
        self.sample_title = viewer_layout.itemAt(0).widget()
        self.viewer = InspectionViewer()
        self.viewer.marker_clicked.connect(self.marker_selected)
        controls = QHBoxLayout()
        self.view_choice = QComboBox()
        for label, key in VIEWS:
            self.view_choice.addItem(label, key)
        self.view_choice.currentIndexChanged.connect(self.change_view)
        controls.addWidget(self.view_choice, 1)
        self.markers_checkbox = QCheckBox("Show markers")
        self.markers_checkbox.setChecked(True)
        self.markers_checkbox.toggled.connect(self.viewer.show_markers)
        controls.addWidget(self.markers_checkbox)
        viewer_layout.addLayout(controls)
        viewer_layout.addWidget(self.viewer, 1)
        self.rotation_controls = RotationControls()
        self.rotation_controls.changed.connect(self.rotate)
        viewer_layout.addWidget(self.rotation_controls)
        self.reinspect_selected = button('Reinspect selected sample', self.reinspect_sample, True)
        self.reinspect_all = button('Reinspect all samples', lambda: controller.reinspect(session), True)
        reinspection = QHBoxLayout()
        reinspection.addWidget(self.reinspect_selected)
        reinspection.addWidget(self.reinspect_all)
        viewer_layout.addLayout(reinspection)
        self.frame_note = QLabel()
        self.frame_note.setWordWrap(True)
        viewer_layout.addWidget(self.frame_note)
        self.splitter.addWidget(viewer_panel)
        detail_panel, detail = panel("Detected errors & review evidence")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Type", "State", "Location", "Details"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 45)
        self.table.setColumnWidth(1, 125)
        self.table.setColumnWidth(2, 115)
        self.table.setColumnWidth(3, 115)
        self.table.itemSelectionChanged.connect(self.table_selected)
        detail.addWidget(self.table, 3)
        self.findings_label = QLabel()
        self.findings_label.setWordWrap(True)
        detail.addWidget(self.findings_label)
        self.zoom_button = button("Zoom to selected error", self.zoom_error, True)
        detail.addWidget(self.zoom_button)
        self.details_tabs = QTabWidget()
        self.details = QPlainTextEdit()
        self.checks = QPlainTextEdit()
        self.metrics = QPlainTextEdit()
        self.reference_text = QPlainTextEdit()
        self.printed_text = QPlainTextEdit()
        self.ocr_differences = QPlainTextEdit()
        for title, edit in (("Selected error", self.details), ("Check statuses", self.checks), ("Metrics", self.metrics),
                            ("Reference OCR", self.reference_text), ("Sample OCR", self.printed_text), ("Text differences", self.ocr_differences)):
            edit.setReadOnly(True)
            self.details_tabs.addTab(edit, title)
        detail.addWidget(self.details_tabs, 2)
        self.splitter.addWidget(detail_panel)
        self.viewer_panel, self.detail_panel = viewer_panel, detail_panel
        self.body.addWidget(self.splitter)
        self.body.addWidget(self.secondary_tabs)
        self.body.setSizes([235, 1140])
        self.splitter.setSizes([720, 420])
        self.root.addWidget(self.body, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 0, 16, 12)
        footer.addWidget(button("Show / hide queue", self.toggle_queue))
        footer.addWidget(button("View reference", self.view_reference))
        self.save_status = QLabel("Saving portable inspection…" if not session.archive_path else "Available offline")
        self.save_status.setWordWrap(True)
        footer.addWidget(self.save_status, 1)
        footer.addWidget(button("Export CSV", self.export_csv))
        footer.addWidget(button("Export JSON report", self.export_json))
        self.root.addLayout(footer)
        controller.saved.connect(self.on_saved)
        controller.changed.connect(self.refresh)
        self.restore_splitters()
        self.queue.setCurrentRow(0)
        self.refresh()

    def refresh(self):
        if not hasattr(self, 'save_status'):
            return
        self.queue.refresh(self.session.samples)
        counts = {}
        for sample in self.session.samples:
            counts[sample['state']] = counts.get(sample['state'], 0) + 1
        self.summary_label.setText(f"{len(self.session.samples)} Total · " + ' · '.join(f'{n} {state}' for state, n in counts.items()))
        self.subtitle.setText(f'{self.session.status} · {len(self.session.samples)} samples')
        locked = self.controller.busy or bool(self.controller.jobs)
        self.reinspect_selected.setEnabled(not locked and not self.session.reference_dirty and self.queue.currentRow() >= 0)
        self.reinspect_all.setEnabled(not locked)
        self.select_sample(self.queue.currentRow())

    def rotate(self, delta):
        index = None if self.view_choice.currentData() == 'normalized_reference' else self.queue.currentRow()
        if index is not None and index < 0:
            return
        self.controller.rotate(self.session, index, delta)

    def reinspect_sample(self):
        if self.queue.currentRow() >= 0:
            self.controller.reinspect(self.session, self.queue.currentRow())

    def on_saved(self, path):
        if self.session.archive_path == path:
            self.save_status.setText("✓ Available offline · inspection saved")
            self.save_status.setToolTip(path)

    def open_saved(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open saved inspection", "", "Portable inspection (*.pinspect)")
        if path:
            self.controller.open(path)

    def save_as(self):
        if self.controller.busy or self.controller.jobs:
            return
        if any(s.get('stale') for s in self.session.samples):
            if QMessageBox.warning(self, 'Stale inspection',
                    'Rotation changed — reinspection required. Save with results marked stale?',
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Save:
                return
        path, _ = QFileDialog.getSaveFileName(self, "Save inspection", self.session.archive_path or "inspection.pinspect", "Portable inspection (*.pinspect)")
        if path:
            if not path.lower().endswith(".pinspect"):
                path += ".pinspect"
            self.save_status.setText("Saving portable inspection…")
            self.controller.save(self.session, path)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "inspection.csv", "CSV (*.csv)")
        if path:
            if any(s.get('stale') for s in self.session.samples):
                QMessageBox.warning(self, 'Reinspection required', 'Reinspect stale results before exporting CSV.')
                return
            results = [model(s["result"]) for s in self.session.samples if s.get("result")]
            self.controller.task(lambda: export_csv(path, results), lambda _: self.save_status.setText("CSV exported"))

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON report", "inspection.json", "JSON (*.json)")
        if path:
            self.controller.task(lambda: write_json(path, self.session.metadata()), lambda _: self.save_status.setText("JSON report exported"))

    def toggle_queue(self):
        self.queue_panel.setVisible(not self.queue_panel.isVisible())

    def view_reference(self):
        self.view_choice.setCurrentIndex(3)

    def apply_filter(self):
        selected = self.filter.currentText()
        first = -1
        for i, sample in enumerate(self.session.samples):
            visible = selected == "All samples" or sample["state"] == selected or (selected == "Failed / incomplete" and sample["state"] in ("Failed", "Cancelled", "Waiting", "Review required"))
            self.queue.item(i).setHidden(not visible)
            if visible and first < 0:
                first = i
        self.queue.setCurrentRow(first)
        if first < 0:
            self.select_sample(-1)

    def select_sample(self, index):
        self.current = None
        self.rows = []
        self.table.setRowCount(0)
        self.viewer.clear()
        for edit in (self.details, self.checks, self.metrics, self.reference_text, self.printed_text, self.ocr_differences):
            edit.clear()
        self.zoom_button.setEnabled(False)
        self.frame_note.clear()
        self.findings_label.clear()
        if index < 0:
            self.sample_title.setText("No samples match this filter")
            return
        self.current = self.session.samples[index]
        self.sample_title.setText(self.current["filename"])
        result = self.current.get("result")
        if self.current.get('stale') or self.current['state'] in ('Waiting', 'Processing'):
            self.findings_label.setText('Rotation changed — reinspection required.' if self.current.get('stale') else self.current['state'])
            self.change_view()
            return
        if not result:
            self.findings_label.setText(f"{self.current['state']} · No completed inspection result")
            self.viewer.load(self.current.get("path"))
            return
        self.rows = result["evidence_rows"]
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            details = row["details"]
            description = details.get("description") or (f"{details.get('expected', '')} → {details.get('detected', '')}" if "expected" in details else json.dumps(details, ensure_ascii=False))
            location = ", ".join(str(v) for v in row["bbox"]) if row.get("bbox") else "Not localized"
            for col, value in enumerate((row["id"], row["type"], row["state"], location, description)):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row_index, col, item)
        clean = self.current["state"] == "Passed" and not result["defects"]
        self.findings_label.setText("✓ No defects detected" if clean else f"{self.current['state']} · Engine decision: {result['status']} · {len(result['defects'])} defects")
        self.findings_label.setToolTip("\n".join(result["decision_reasons"]))
        self.checks.setPlainText("\n\n".join(f"{name.replace('_', ' ').capitalize()}: {check.get('status', 'Unavailable')}\n{check.get('reason', '')}" for name, check in result["checks"].items()) + "\n\nDecision reasons\n" + "\n".join(result["decision_reasons"]) + "\n\nWarnings\n" + "\n".join(result["warnings"]))
        self.metrics.setPlainText(json.dumps({"metrics": result["metrics"], "duration_seconds": result["processing_time_seconds"], "alignment": result["alignment"], "orientation": result["orientation"]}, ensure_ascii=False, indent=2))
        for key, edit in (("reference", self.reference_text), ("printed", self.printed_text)):
            ocr = result["ocr"].get(key, {})
            edit.setPlainText(ocr.get("strict_text") or ocr.get("error") or "No OCR transcription available")
        self.ocr_differences.setPlainText(json.dumps(result["ocr"].get("comparison", {}), ensure_ascii=False, indent=2))
        self.change_view()

    def change_view(self, *_):
        if not self.current:
            return
        key = self.view_choice.currentData()
        source_view = key in {'aligned_printed', 'original_printed', 'normalized_printed', 'normalized_reference'}
        self.rotation_controls.setEnabled(source_view and not self.controller.busy and not self.controller.jobs)
        if self.current.get('stale') or self.current['state'] in ('Waiting', 'Processing'):
            index = None if key == 'normalized_reference' else self.queue.currentRow()
            if source_view:
                path = self.session.reference if index is None else self.current['path']
                self.viewer.load_source(path, self.session.rotation(index).get('correction_clockwise', 0))
            else:
                self.viewer.clear('Diagnostic unavailable until reinspection')
            self.frame_note.setText('Reference rotation changed — reinspection required for the entire batch.' if self.session.reference_dirty else 'Rotation changed — reinspection required. Outdated markers and metrics are hidden.')
            return
        result = self.current.get("result") or {}
        path = result.get("artifacts", {}).get(key)
        if key == "original_printed":
            path = path or self.current.get("path")
        if key == "normalized_reference":
            path = path or self.session.reference
        self.viewer.load(path)
        localized = key in CANONICAL and bool(result.get("artifacts", {}).get(key))
        self.viewer.set_markers(self.rows if localized else [], self.markers_checkbox.isChecked())
        self.frame_note.setText("Markers: normalized reference coordinates" if localized else "Original / normalized sample view · markers hidden outside the comparison frame")

    def table_selected(self):
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self.rows):
            return
        row = self.rows[row_index]
        self.viewer.select_marker(row["id"])
        self.details.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))
        self.zoom_button.setEnabled(bool(row.get("bbox")))

    def marker_selected(self, identity):
        for index, row in enumerate(self.rows):
            if row["id"] == identity:
                self.table.selectRow(index)
                self.table.scrollToItem(self.table.item(index, 0))
                break

    def zoom_error(self):
        index = self.table.currentRow()
        if index >= 0:
            identity = self.rows[index]["id"]
            self.view_choice.setCurrentIndex(0)
            self.viewer.select_marker(identity, True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "detail_panel"):
            return
        narrow = self.width() < 1150
        if self.narrow != narrow:
            self.narrow = narrow
            if narrow:
                self.secondary_tabs.addTab(self.viewer_panel, "Sample image")
                self.secondary_tabs.addTab(self.detail_panel, "Errors & details")
            else:
                self.splitter.addWidget(self.viewer_panel)
                self.splitter.addWidget(self.detail_panel)
                self.viewer_panel.show()
                self.detail_panel.show()
                self.splitter.setSizes([720, 420])
            self.splitter.setVisible(not narrow)
            self.secondary_tabs.setVisible(narrow)
            self.queue_panel.setVisible(not narrow)
            self.body.setSizes([235, 1140, 0] if not narrow else [0, 0, 1140])
