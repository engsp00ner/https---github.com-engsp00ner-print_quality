"""Own the active worker independently of the three presentation windows."""
from pathlib import Path
import platform
from config import Settings
from PySide6.QtCore import QObject, Signal
from .worker import InspectionWorker
from .components import Task
from src.inspection_session import InspectionSession, evidence_rows, result_state, timestamp
from src.inspection_archive import save_inspection, load_inspection


class InspectionController(QObject):
    changed = Signal()
    stage = Signal(object)
    started_sample = Signal(int)
    completed = Signal(object)
    saved = Signal(str)
    opened = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.session = None
        self.jobs = []
        self.engine = None
        self.indices = []

    @property
    def busy(self):
        return self.worker is not None

    def task(self, action, callback):
        job = Task(action, self)
        self.jobs.append(job)
        self.changed.emit()
        job.completed.connect(callback)
        job.failed.connect(self.error.emit)
        job.finished.connect(lambda: self.dispose(job))
        job.start()

    def dispose(self, job):
        self.jobs.remove(job)
        job.deleteLater()
        self.changed.emit()

    def start(self, reference, paths, settings, reference_angle=None, angles=None):
        if self.busy:
            return False
        self.session = InspectionSession.create(reference, paths, settings)
        self.engine = None
        self.indices = list(range(len(paths)))
        if reference_angle is not None:
            self.session.reference_rotation = dict(status='MANUAL', method='manual', correction_clockwise=reference_angle, override=reference_angle)
        for sample in self.session.samples:
            angle = (angles or {}).get(sample['path'])
            if angle is not None:
                sample['rotation'] = dict(status='MANUAL', method='manual', correction_clockwise=angle, override=angle)
        self.session.runtime = {"python": platform.python_version(), "platform": platform.platform()}
        self.worker = InspectionWorker(reference, paths, settings, self, reference_angle, angles)
        self.connect_worker()
        return True

    def connect_worker(self):
        self.worker.sample_started.connect(self.sample_started)
        self.worker.inputs_saved.connect(self.inputs_saved)
        self.worker.stage_changed.connect(self.stage.emit)
        self.worker.image_finished.connect(self.image_finished)
        self.worker.output_ready.connect(self.output_ready)
        self.worker.error_occurred.connect(self.error.emit)
        self.worker.batch_finished.connect(self.batch_finished)
        self.worker.finished.connect(self.worker_stopped)
        self.changed.emit()
        self.worker.start()

    def rotate(self, session, index, delta):
        if self.busy or self.jobs:
            return False
        session.set_rotation(index, delta)
        self.changed.emit()
        return True

    def reinspect(self, session, index=None):
        if self.busy or self.jobs or (index is not None and session.reference_dirty):
            return False
        if session is not self.session:
            self.engine = None
        self.session = session
        self.indices = list(range(len(session.samples))) if index is None else [index]
        def override(info):
            return info.get('override', info.get('correction_clockwise')) if info.get('status') == 'MANUAL' else None
        paths = [Path(session.samples[i]['path']) for i in self.indices]
        angles = {str(paths[j]): override(session.rotation(i)) for j, i in enumerate(self.indices)}
        self.worker = InspectionWorker(session.reference, paths, Settings(**session.settings), self,
                                       override(session.rotation()), angles)
        self.worker.engine = self.engine
        self.worker.reinspection = True
        session.status = 'Running'
        session.error = ''
        for i in self.indices:
            session.samples[i]['state'] = 'Waiting'
        self.connect_worker()
        return True

    def output_ready(self, folder):
        self.session.folder = folder

    def inputs_saved(self, snapshots):
        self.snapshots = snapshots
        self.session.reference = snapshots.get(self.session.reference, self.session.reference)
        for sample in self.session.samples:
            sample["path"] = snapshots.get(sample["path"], sample["path"])

    def sample_started(self, index, path):
        index = self.indices[index]
        self.session.samples[index]["state"] = "Processing"
        self.changed.emit()
        self.started_sample.emit(index)

    def image_finished(self, result):
        sample = next(s for s in self.session.samples if s["state"] == "Processing")
        data = result.to_dict()
        data["reference_path"] = self.session.reference
        data["printed_path"] = sample["path"]
        data["evidence_rows"] = evidence_rows(data)
        frame = data.get("orientation", {}).get("comparison_frame", "normalized_reference")
        data["coordinate_frames"] = {"markers": frame, "original_printed": "loaded_printed",
                                     "aligned_printed": frame, "normalized_printed": "normalized_printed"}
        sample.update(result=data, state=result_state(data), stale=False,
                      rotation=data.get('orientation', {}).get('printed', sample.get('rotation', {})))
        if data.get('orientation', {}).get('reference'):
            self.session.reference_rotation = data['orientation']['reference']
        self.changed.emit()

    def batch_finished(self, summary):
        session = self.session
        session.duration = summary["duration"]
        session.error = summary["error"]
        session.finished_at = timestamp()
        session.status = "Cancelled" if summary["cancelled"] else "Failed" if summary["error"] else "Completed"
        for i in self.indices:
            sample = session.samples[i]
            if sample["state"] in ("Waiting", "Processing"):
                sample["state"] = 'Stale' if sample.get('stale') else "Cancelled" if summary["cancelled"] else "Failed"
        if not summary['cancelled'] and not summary['error'] and len(self.indices) == len(session.samples):
            session.reference_dirty = False
        self.changed.emit()
        # Keep the worker alive until its finished signal, including report writing.

    def worker_stopped(self):
        worker, self.worker = self.worker, None
        self.engine = worker.engine
        if self.engine:
            self.engine.progress_callback = None
        worker.deleteLater()
        session = self.session
        self.changed.emit()
        self.completed.emit(session)
        if session.folder:
            session.autosave_path = session.autosave_path or str(Path(session.folder) / f"inspection_{session.batch_id}.pinspect")
            self.save(session, session.autosave_path)

    def cancel(self):
        if self.worker:
            self.worker.requestInterruption()
            self.stage.emit({"stage": "Cancellation requested — finishing current sample", "status": "RUNNING"})

    def save(self, session, path):
        def done(saved):
            session.archive_path = saved
            self.saved.emit(saved)
        self.task(lambda: save_inspection(path, session), done)

    def open(self, path):
        self.task(lambda: load_inspection(path), self.opened.emit)
