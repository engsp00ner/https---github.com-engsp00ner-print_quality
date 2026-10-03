"""Display state and evidence projection; no analysis is performed here."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def result_state(result):
    if result.get('stale'):
        return 'Stale'
    if "Processing error; manual review required" in result.get("decision_reasons", []):
        return "Failed"
    if result.get("status") == "PASS" and result.get("inspection_complete"):
        return "Passed"
    if not result.get("inspection_complete"):
        return "Review required"
    return "Defective"


def evidence_rows(result):
    """Stable per-sample IDs. Auxiliary evidence is never promoted to defects."""
    rows = []
    if result.get('stale'):
        return rows
    for i, defect in enumerate(result.get("defects", []), 1):
        details = defect.get("details", {})
        review = details.get("needs_review", False) or (
            defect["type"] == "TEXT_ERROR" and not result.get("metrics", {}).get("text_mismatch_reliable", True))
        box = [defect[k] for k in ("x", "y", "width", "height")]
        rows.append({"id": str(i), "type": ", ".join(details.get("labels", [defect["type"]])),
                     "state": "Review required" if review else "Confirmed", "bbox": box,
                     "frame": "normalized_reference", "details": details})
        for j, evidence in enumerate(details.get("evidence", []), 1):
            if evidence.get("classification") == "text_change_visual_evidence":
                rows.append({"id": f"{i}.{j}", "type": evidence["type"], "state": "Review required",
                             "bbox": evidence.get("bbox"), "frame": "normalized_reference",
                             "details": {**evidence.get("details", {}), "description": "Visual evidence overlapping text change"}})
    # Non-localized text differences must remain visible when registration was unreliable.
    if not any(d["type"] == "TEXT_ERROR" for d in result.get("defects", [])):
        for i, change in enumerate(result.get("ocr", {}).get("comparison", {}).get("word_differences", []), 1):
            rows.append({"id": f"T{i}", "type": "Text difference", "state": "Review required",
                         "bbox": None, "frame": None, "details": change})
    for i, reason in enumerate(result.get("decision_reasons", []), 1):
        if reason != "Localized defects detected":
            rows.append({"id": f"R{i}", "type": "Inspection decision", "state": "Review required" if not result.get("inspection_complete") else "Confirmed",
                         "bbox": None, "frame": None, "details": {"description": reason}})
    return rows


@dataclass
class InspectionSession:
    reference: str
    samples: list
    settings: dict
    batch_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=timestamp)
    finished_at: str = ""
    status: str = "Running"
    folder: str = ""
    error: str = ""
    duration: float = 0
    archive_path: str = ""
    runtime: dict = field(default_factory=dict)
    storage: object = field(default=None, repr=False)
    reference_rotation: dict = field(default_factory=dict)
    reference_dirty: bool = False
    autosave_path: str = ""

    @classmethod
    def create(cls, reference, paths, settings):
        return cls(str(reference), [{"id": str(i + 1), "filename": p.name, "path": str(p),
                    "state": "Waiting", "result": None} for i, p in enumerate(paths)], settings.to_dict())

    def metadata(self):
        return {key: getattr(self, key) for key in ("reference", "samples", "settings", "batch_id", "created_at",
                "finished_at", "status", "folder", "error", "duration", "runtime",
                "reference_rotation", "reference_dirty")}

    def rotation(self, index=None):
        if index is None:
            if self.reference_rotation:
                return self.reference_rotation
            for sample in self.samples:
                info = (sample.get('result') or {}).get('orientation', {}).get('reference')
                if info:
                    return info
            return {}
        sample = self.samples[index]
        return sample.get('rotation') or (sample.get('result') or {}).get('orientation', {}).get('printed', {})

    def set_rotation(self, index, delta):
        from PIL import Image, ImageOps
        from .orientation import rotation_matrix
        import numpy as np
        info = self.rotation(index)
        angle = (info.get('correction_clockwise', 0) + delta) % 360 if delta is not None else 0
        path = self.reference if index is None else self.samples[index]['path']
        with Image.open(path) as source:
            w, h = ImageOps.exif_transpose(source).size
        matrix = rotation_matrix((h, w), angle)
        value = dict(correction_clockwise=angle, override=angle if delta is not None else None,
                     status='MANUAL' if delta is not None else 'PENDING_AUTO',
                     method='manual' if delta is not None else 'auto',
                     original_size=[w, h], normalized_size=[h, w] if angle % 180 else [w, h],
                     forward_transform=matrix.tolist(), inverse_transform=np.linalg.inv(matrix).tolist(),
                     source_frame='loaded_exif_corrected', destination_frame='normalized',
                     coordinate_convention='pixel_centers')
        if index is None:
            self.reference_rotation = value
            self.reference_dirty = True
            affected = self.samples
        else:
            self.samples[index]['rotation'] = value
            affected = [self.samples[index]]
        for sample in affected:
            sample.update(stale=True, state='Stale')
            if sample.get('result'):
                sample['result']['stale'] = True
                sample['result']['inspection_complete'] = False
                sample['result']['status'] = 'DEFECTIVE'
        return angle
