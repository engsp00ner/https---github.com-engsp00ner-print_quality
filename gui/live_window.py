from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSplitter, QTabWidget, QProgressBar
from .components import DesktopWindow, SampleList, InspectionViewer, panel, button, RotationControls


class LiveWindow(DesktopWindow):
    def __init__(self, controller):
        super().__init__("live", "Inspection in progress", "Preparing reference")
        self.controller = controller
        self.session = controller.session
        self.queue_toggle = button("Hide queue", self.toggle_queue)
        self.header.addWidget(self.queue_toggle)
        self.body = QSplitter()
        self.queue_panel, q = panel(f"Sample queue ({len(self.session.samples)})")
        self.queue = SampleList()
        self.queue.populate(self.session.samples)
        q.addWidget(self.queue)
        self.body.addWidget(self.queue_panel)
        self.preview_area = QWidget()
        pv = QVBoxLayout(self.preview_area)
        pv.setContentsMargins(0, 0, 12, 0)
        self.splitter = QSplitter()
        self.reference = InspectionViewer("Reference")
        self.sample = InspectionViewer("Current sample")
        self.reference_panel, r = panel("Reference")
        r.addWidget(self.reference)
        self.reference_rotation_controls = RotationControls()
        self.reference_rotation_controls.changed.connect(lambda delta: controller.rotate(self.session, None, delta))
        r.addWidget(self.reference_rotation_controls)
        self.sample_panel, s = panel("Current sample")
        s.addWidget(self.sample)
        self.sample_rotation_controls = RotationControls()
        self.sample_rotation_controls.changed.connect(self.rotate_sample)
        s.addWidget(self.sample_rotation_controls)
        self.splitter.addWidget(self.reference_panel)
        self.splitter.addWidget(self.sample_panel)
        self.splitter.setSizes([450, 580])
        self.tabs = QTabWidget()
        self.tabs.hide()
        pv.addWidget(self.splitter, 1)
        pv.addWidget(self.tabs, 1)
        stage_panel, stages = panel("Live inspection stage")
        self.stage_label = QLabel("Reference preparation · Waiting")
        self.stage_label.setWordWrap(True)
        stages.addWidget(self.stage_label)
        self.stage_history = QLabel()
        self.stage_history.setWordWrap(True)
        stages.addWidget(self.stage_history)
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setMaximumHeight(8)
        stages.addWidget(self.activity)
        pv.addWidget(stage_panel)
        self.body.addWidget(self.preview_area)
        self.body.setSizes([255, 1100])
        self.root.addWidget(self.body, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(18, 6, 18, 14)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(self.session.samples))
        self.progress.setFormat("%v of %m completed")
        footer.addWidget(self.progress, 1)
        self.cancel_button = button("Cancel after current sample", self.cancel)
        footer.addWidget(self.cancel_button)
        self.reinspect_selected = button('Reinspect selected sample', self.reinspect_sample)
        self.reinspect_all = button('Reinspect all samples', lambda: controller.reinspect(self.session))
        footer.addWidget(self.reinspect_selected)
        footer.addWidget(self.reinspect_all)
        self.root.addLayout(footer)
        self.reference.load(self.session.reference)
        self.stages = {}
        self.narrow = False
        controller.started_sample.connect(self.start_sample)
        controller.stage.connect(self.stage_changed)
        controller.changed.connect(self.refresh)
        self.restore_splitters()
        self.statusBar().showMessage("Closing this window hides it; inspection continues. Reopen it from Setup.")
        self.queue.currentRowChanged.connect(lambda _: self.refresh())
        self.refresh()

    def rotate_sample(self, delta):
        index = self.queue.currentRow()
        if index >= 0:
            self.controller.rotate(self.session, index, delta)

    def reinspect_sample(self):
        index = self.queue.currentRow()
        if index >= 0:
            self.controller.reinspect(self.session, index)

    def toggle_queue(self):
        self.queue_panel.setVisible(not self.queue_panel.isVisible())
        self.queue_toggle.setText("Hide queue" if self.queue_panel.isVisible() else "Show queue")

    def start_sample(self, index):
        if self.controller.session is not self.session:
            return
        sample = self.session.samples[index]
        self.subtitle.setText(f"Sample {index + 1:02d} of {len(self.session.samples)}  ·  {sample['filename']}")
        self.queue.setCurrentRow(index)
        self.sample.load(sample["path"])
        self.sample.start_scan()
        self.stages = {}
        self.stage_history.clear()

    def stage_changed(self, event):
        if self.controller.session is not self.session:
            return
        name, status = event["stage"], event["status"]
        self.stages[name] = status
        self.stage_label.setText(f"{name.replace('_', ' ').capitalize()} · {status.replace('_', ' ').lower()}" +
                                 (f" — {event['reason']}" if event.get("reason") else ""))
        self.stage_history.setText("   ·   ".join(f"{'✓' if s == 'COMPLETED' else '!' if s == 'FAILED' else '–' if s == 'SKIPPED' else '◉'} {n.replace('_', ' ')}" for n, s in self.stages.items()))

    def refresh(self):
        locked = self.controller.busy or bool(self.controller.jobs)
        index = self.queue.currentRow()
        self.reference_rotation_controls.setEnabled(not locked)
        self.sample_rotation_controls.setEnabled(not locked and index >= 0)
        self.reinspect_selected.setEnabled(not locked and index >= 0 and not self.session.reference_dirty)
        self.reinspect_all.setEnabled(not locked)
        self.queue.refresh(self.session.samples)
        self.progress.setValue(sum(bool(s.get("result")) for s in self.session.samples))
        if not any(s["state"] == "Processing" for s in self.session.samples):
            self.sample.stop_scan()
            completed = [s["result"] for s in self.session.samples if s.get("result") and not s.get('stale')]
            if completed:
                result = completed[-1]
                if result["artifacts"].get("aligned_printed"):
                    self.sample.load(result["artifacts"]["aligned_printed"])
                    self.reference.load(result["artifacts"]["normalized_reference"])
        if self.session.status != "Running":
            self.activity.setRange(0, 1)
            self.activity.setValue(1)
            self.cancel_button.setEnabled(False)
            self.heading.setText(f"Inspection {self.session.status.lower()}")
            self.stage_label.setText("Results available in the Results window")
        if not self.controller.busy:
            self.reference.load_source(self.session.reference, self.session.rotation().get('correction_clockwise', 0))
            if index >= 0:
                self.sample.load_source(self.session.samples[index]['path'], self.session.rotation(index).get('correction_clockwise', 0))
            if any(s.get('stale') for s in self.session.samples):
                self.stage_label.setText('Rotation changed — reinspection required.' + (' Reference change affects the entire batch.' if self.session.reference_dirty else ''))

    def cancel(self):
        self.controller.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Finishing current sample…")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "tabs"):
            return
        narrow = self.width() < 1100
        if narrow != self.narrow:
            self.narrow = narrow
            self.queue_panel.setVisible(not narrow)
            self.queue_toggle.setText("Show queue" if narrow else "Hide queue")
            if narrow:
                self.tabs.addTab(self.reference_panel, "Reference")
                self.tabs.addTab(self.sample_panel, "Current sample")
                self.tabs.setCurrentIndex(1)
            else:
                self.splitter.addWidget(self.reference_panel)
                self.splitter.addWidget(self.sample_panel)
                self.reference_panel.show()
                self.sample_panel.show()
                self.splitter.setSizes([450, 580])
            self.tabs.setVisible(narrow)
            self.splitter.setVisible(not narrow)

    def closeEvent(self, event):
        self.remember()
        self.hide()
        event.ignore()
