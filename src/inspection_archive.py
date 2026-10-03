"""Versioned, bounded, atomic portable inspections. Deliberately analysis-free."""
import copy
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile
from PIL import Image
from .inspection_session import InspectionSession

SCHEMA = 1
MAX_FILES = 20000
MAX_TOTAL = 4 * 1024**3
MAX_ASSET = 256 * 1024**2
MAX_METADATA = 32 * 1024**2
STATES = {"Waiting", "Processing", "Passed", "Defective", "Review required", "Failed", "Cancelled", "Stale"}


def asset_slots(data):
    yield data, "reference", True
    for sample in data["samples"]:
        yield sample, "path", sample["state"] not in ("Failed", "Cancelled", "Waiting")
        result = sample.get("result")
        if result:
            yield result, "reference_path", True
            yield result, "printed_path", sample["state"] != "Failed"
            for key in result["artifacts"]:
                yield result["artifacts"], key, True


def save_inspection(path, session):
    path = Path(path)
    data = copy.deepcopy(session.metadata())
    data.update(schema_version=SCHEMA, application_version="print-inspection-desktop-1")
    data["folder"] = ""
    assets = {}
    for owner, key, required in asset_slots(data):
        source = Path(owner.get(key) or "")
        if not source.is_file():
            if required:
                raise ValueError(f"Required image is unavailable: {source}")
            owner[key] = ""
            continue
        resolved = str(source.resolve())
        if resolved not in assets:
            assets[resolved] = f"assets/{len(assets):06d}{source.suffix.lower()}"
        owner[key] = assets[resolved]
    for sample in data["samples"]:
        if sample.get("result"):
            sample["result"]["output_dir"] = ""
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_METADATA or len(assets) > MAX_FILES - 1:
        raise ValueError("Inspection exceeds archive limits")
    sizes = [Path(p).stat().st_size for p in assets]
    if any(s > MAX_ASSET for s in sizes) or sum(sizes) > MAX_TOTAL:
        raise ValueError("Inspection images exceed archive limits")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                archive.writestr("inspection.json", payload)
                for source, name in assets.items():
                    archive.write(source, name)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path.resolve())


def safe_name(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name and str(p) == name


def asset_name(name):
    return isinstance(name, str) and re.fullmatch(r"assets/[0-9]{6}\.(png|jpg|jpeg|bmp|tif|tiff)", name) is not None


def validate_metadata(data, names):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported .pinspect schema version")
    for name in ("batch_id", "created_at", "finished_at", "status", "reference", "folder", "error"):
        if not isinstance(data.get(name), str):
            raise ValueError(f"Invalid metadata field: {name}")
    if data["status"] not in ("Completed", "Cancelled", "Failed"):
        raise ValueError("Archive does not describe a finished or partial run")
    if not isinstance(data.get("settings"), dict) or not isinstance(data.get("runtime"), dict):
        raise ValueError("Invalid settings/runtime metadata")
    if not isinstance(data.get("duration"), (int, float)) or data["duration"] < 0:
        raise ValueError("Invalid duration")
    if not isinstance(data.get("samples"), list) or len(data["samples"]) > 10000:
        raise ValueError("Invalid sample list")
    ids = set()
    for sample in data["samples"]:
        if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or sample["id"] in ids:
            raise ValueError("Invalid or duplicate sample ID")
        ids.add(sample["id"])
        if sample.get("state") not in STATES or not isinstance(sample.get("filename"), str):
            raise ValueError("Invalid sample state or filename")
        result = sample.get("result")
        if sample["state"] in ("Passed", "Defective", "Review required") and not result:
            raise ValueError("Completed sample is missing its result")
        if result is not None:
            if not isinstance(result, dict):
                raise ValueError("Invalid result")
            for field in ("artifacts", "checks", "metrics", "ocr", "orientation", "alignment"):
                if not isinstance(result.get(field), dict):
                    raise ValueError(f"Invalid result {field}")
            for field in ("defects", "warnings", "decision_reasons", "evidence_rows"):
                if not isinstance(result.get(field), list):
                    raise ValueError(f"Invalid result {field}")
            if not isinstance(result.get("inspection_complete"), bool) or result.get("status") not in ("PASS", "DEFECTIVE"):
                raise ValueError("Invalid engine decision")
            for field in ("filename", "printed_path", "reference_path", "output_dir"):
                if not isinstance(result.get(field), str):
                    raise ValueError(f"Invalid result field: {field}")
            if not isinstance(result.get("processing_time_seconds"), (int, float)):
                raise ValueError("Invalid result duration")
            if not all(isinstance(v, str) for v in result["warnings"] + result["decision_reasons"]):
                raise ValueError("Invalid warnings or reasons")
            if not all(isinstance(v, dict) for v in result["checks"].values()):
                raise ValueError("Invalid check statuses")
            if not all(isinstance(v, dict) for v in result["ocr"].values()):
                raise ValueError("Invalid OCR metadata")
            for defect in result["defects"]:
                if not isinstance(defect, dict) or not isinstance(defect.get("type"), str) or not isinstance(defect.get("details"), dict):
                    raise ValueError("Invalid defect metadata")
                if not all(isinstance(defect.get(k), (int, float)) for k in ("x", "y", "width", "height", "confidence")):
                    raise ValueError("Invalid defect geometry")
            row_ids = set()
            for row in result["evidence_rows"]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("details"), dict):
                    raise ValueError("Invalid evidence row")
                if row["id"] in row_ids or not isinstance(row.get("type"), str) or row.get("state") not in ("Confirmed", "Review required"):
                    raise ValueError("Invalid evidence ID, type or state")
                row_ids.add(row["id"])
                box = row.get("bbox")
                if box is not None and row.get("frame") != "normalized_reference":
                    raise ValueError("Unsupported marker coordinate frame")
                if box is not None and (not isinstance(box, list) or len(box) != 4 or
                        not all(isinstance(n, (int, float)) and abs(n) <= 1000000 for n in box) or box[2] <= 0 or box[3] <= 0):
                    raise ValueError("Invalid marker geometry")
    for owner, key, required in asset_slots(data):
        name = owner.get(key)
        if not name and not required:
            continue
        if not asset_name(name) or name not in names:
            raise ValueError(f"Missing or invalid required image: {name}")


def load_inspection(path):
    storage = tempfile.TemporaryDirectory(prefix="pinspect_")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [info.filename for info in entries]
            if len(entries) > MAX_FILES or len(set(names)) != len(names) or any(not safe_name(n) for n in names):
                raise ValueError("Unsafe paths or duplicate entries in archive")
            if any(n != "inspection.json" and not asset_name(n) for n in names):
                raise ValueError("Unexpected archive member")
            if any(i.file_size > MAX_ASSET or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in entries) or sum(i.file_size for i in entries) > MAX_TOTAL:
                raise ValueError("Archive exceeds safe resource limits or contains links")
            if "inspection.json" not in names or archive.getinfo("inspection.json").file_size > MAX_METADATA:
                raise ValueError("Missing or oversized inspection metadata")
            data = json.loads(archive.read("inspection.json").decode("utf-8"),
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")))
            validate_metadata(data, set(names))
            needed = {owner[key] for owner, key, _ in asset_slots(data) if owner.get(key)}
            for name in needed:
                target = Path(storage.name) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                with Image.open(target) as im:
                    if im.width * im.height > 40_000_000:
                        raise ValueError("Embedded image exceeds pixel limit")
                    im.verify()
            for owner, key, _ in asset_slots(data):
                if owner.get(key):
                    owner[key] = str(Path(storage.name) / owner[key])
        fields = {key: data[key] for key in ("reference", "samples", "settings", "batch_id", "created_at", "finished_at", "status", "folder", "error", "duration", "runtime")}
        return InspectionSession(**fields, reference_rotation=data.get('reference_rotation', {}),
                                 reference_dirty=data.get('reference_dirty', False),
                                 storage=storage, archive_path=str(Path(path).resolve()))
    except Exception as exc:
        storage.cleanup()
        raise ValueError(f"Cannot open inspection: {exc}") from exc
