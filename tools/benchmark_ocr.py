"""Create a local, fair Arabic OCR benchmark for Tesseract, EasyOCR and PaddleOCR.

The application environment runs this coordinator and Tesseract. EasyOCR and
PaddleOCR run in separately supplied Python environments so their dependencies
cannot change inspection behavior. The generated HTML is self-contained: each
OCR image is immediately followed by that exact engine's unedited text.
"""
import argparse
import base64
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from unicodedata import normalize as unicode_normalize

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_settings
from src.image_loader import load_image, save_image
from src.ocr_engine import OrientationOCR as OCREngine
from src.orientation import OrientationDetector, RESOLVED
from src.reporting import create_batch_dir, write_json

DEFAULT_EASY = Path.home() / ".na3em_ocr_benchmark" / "easy" / "Scripts" / "python.exe"
DEFAULT_PADDLE = Path.home() / ".na3em_ocr_benchmark" / "paddle" / "Scripts" / "python.exe"

# Coordinates are in the 1131 x 1600 normalized upright page and include padding.
# Text has been visually verified against the source except the form code, which is
# intentionally excluded from accuracy scoring.
CROPS = [
    ("title", (360, 65, 410, 90), "دفتر عهدة المخزن", "psm7"),
    ("item_number", (740, 130, 260, 75), "رقم الصنف", "psm7"),
    ("item_name", (455, 130, 265, 75), "إسم الصنف", "psm7"),
    ("unit", (270, 130, 170, 75), "الوحدة", "psm7"),
    ("balance", (565, 225, 205, 120), "الباقي بالعهدة", "psm6"),
    ("incoming_outgoing", (760, 220, 250, 130), "أو منصرف إلى", "psm6"),
    ("notes", (65, 235, 135, 100), "ملاحظات", "psm7"),
]


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bbox_from_polygon(points):
    if not points:
        return None
    values = np.asarray(points, dtype=float).reshape(-1, 2)
    if not len(values):
        return None
    left, top = values.min(axis=0)
    right, bottom = values.max(axis=0)
    return [float(left), float(top), float(right - left), float(bottom - top)]


def add_offset(region, offset):
    answer = dict(region)
    bbox = bbox_from_polygon(region.get("bbox", []))
    if bbox is not None:
        answer["page_bbox"] = [bbox[0] + offset[0], bbox[1] + offset[1], bbox[2], bbox[3]]
    return answer


def overlay(image, regions, path):
    result = image.copy()
    for index, region in enumerate(regions, 1):
        bbox = bbox_from_polygon(region.get("bbox", [])) or region.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x, y, w, h = (round(v) for v in bbox)
        cv2.rectangle(result, (max(0, x), max(0, y)), (min(result.shape[1] - 1, x + max(1, w)),
                      min(result.shape[0] - 1, y + max(1, h))), (0, 0, 255), 2)
        cv2.putText(result, str(index), (max(0, x), max(15, y - 3)), cv2.FONT_HERSHEY_SIMPLEX,
                    .6, (255, 0, 0), 2, cv2.LINE_AA)
    save_image(path, result)


def text_from_regions(regions):
    # Native engine order; do not sort Arabic words or reverse text.
    return "\n".join(region.get("text", "") for region in regions if region.get("text", "") != "")


def tesseract_result(ocr, image, psm):
    result = ocr.extract(image, psm=psm)
    regions = [{"bbox": [word.bbox[0], word.bbox[1], word.bbox[2], word.bbox[3]],
                "text": word.text, "confidence": word.confidence, "line_id": list(word.line_id)}
               for word in result.words]
    return {"status": result.status, "error": result.error, "text": result.strict_text,
            "regions": regions, "native": asdict(result), "inference_seconds": None}


def normalized_for_evaluation(text):
    return " ".join(unicode_normalize("NFC", text).split())


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def crop_evaluation(expected, actual):
    expected, actual = normalized_for_evaluation(expected), normalized_for_evaluation(actual)
    if not actual:
        status = "Missing"
    elif actual == expected:
        status = "Exact"
    else:
        status = "Incorrect"
    return {"expected": expected, "actual": actual, "status": status,
            "character_error_rate": edit_distance(expected, actual) / max(1, len(expected))}


def img_data(path):
    suffix = Path(path).suffix.lower()
    mime = ".png" if suffix == ".png" else suffix
    return f"data:image/{mime[1:]};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def card(title, result, image_path):
    status = result["status"]
    actual = result.get("text", "")
    body = actual if actual else "No text detected." if status == "SUCCESS" else result.get("error", "Execution failed.")
    warning = result.get("error", "")
    return f'''<article class="card"><h3>{html.escape(title)}</h3><p class="status {status.lower()}">{html.escape(status)}</p>
<a href="{img_data(image_path)}" target="_blank"><img src="{img_data(image_path)}" alt="{html.escape(title)} OCR overlay"></a>
<h4>Actual recognized text</h4><pre dir="auto">{html.escape(body)}</pre>
<p>Regions: {len(result.get("regions", []))} · Characters: {len(actual)} · Inference: {result.get("inference_seconds", 0) or 0:.3f}s</p>
{f'<p class="warning">{html.escape(warning)}</p>' if warning else ''}</article>'''


def html_report(report, page_results, crop_results, image_paths, out_path):
    top = "".join(f'<figure><img src="{img_data(path)}"><figcaption>{html.escape(label)}</figcaption></figure>'
                  for label, path in (("Original input", image_paths["original"]), ("Normalized upright image", image_paths["upright"])))
    full = "".join(card(name, result, image_paths["full_overlays"][name]) for name, result in page_results.items())
    crops = []
    for crop in CROPS:
        crop_id, _, expected, _ = crop
        crop_cards = "".join(card(name, result, image_paths["crop_overlays"][crop_id][name])
                             for name, result in crop_results[crop_id].items())
        evaluation = report["evaluation"][crop_id]
        rows = "".join(f"<tr><td>{html.escape(name)}</td><td>{html.escape(item['status'])}</td><td dir=\"auto\">{html.escape(item['actual'])}</td><td>{item['character_error_rate']:.3f}</td></tr>"
                       for name, item in evaluation.items())
        crops.append(f'<section><h2>{html.escape(crop_id)} <span dir="auto">{html.escape(expected)}</span></h2><div class="grid">{crop_cards}</div><table><tr><th>Engine</th><th>Evaluation</th><th>Actual native-order text</th><th>CER</th></tr>{rows}</table></section>')
    report_json = html.escape(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Arabic OCR comparison</title>
<style>body{{font:16px/1.45 Segoe UI,Arial,sans-serif;margin:24px;background:#f5f7fa;color:#172033}}h1,h2{{margin-top:32px}}.top,.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}}figure,.card{{margin:0;background:#fff;border:1px solid #d9e0ea;border-radius:10px;padding:14px;box-shadow:0 1px 3px #0001}}img{{width:100%;height:auto;display:block;border:1px solid #d9e0ea}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px;min-height:56px;font:16px/1.5 Consolas,"Noto Naskh Arabic",Arial,sans-serif}}.status{{font-weight:700}}.success{{color:#146c43}}.failed{{color:#b42318}}.warning{{color:#8a5800}}table{{width:100%;border-collapse:collapse;background:#fff;margin-top:16px}}td,th{{padding:8px;border:1px solid #d9e0ea;text-align:left;vertical-align:top}}code{{white-space:pre-wrap}}</style>
<h1>Arabic OCR comparison</h1><p>This report embeds all images. Every image is followed immediately by the actual, unedited text returned by that engine for those same pixels. Arabic text is stored logically; no text was reversed or corrected.</p>
<div class="top">{top}</div><h2>Input and shared settings</h2><pre>{report_json}</pre><h2>Full-page OCR</h2><div class="grid">{full}</div><h2>Crop-based OCR</h2>{''.join(crops)}
<h2>Evaluation scope</h2><p>Only the seven visually verified headings listed beside each crop are scored. The empty table cells are not evaluated. Full-page transcription has no complete ground truth and is not assigned an accuracy score. Confidence values are shown in raw JSON only and are not compared across engines.</p></html>'''
    out_path.write_text(document, encoding="utf-8")


def run_external(engine, interpreter, manifest, output):
    command = [str(interpreter), str(ROOT / "tools" / "ocr_benchmark_worker.py"), "--engine", engine,
               "--manifest", str(manifest), "--output", str(output)]
    launched = time.perf_counter()
    try:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
                                   errors="replace", timeout=900)
        record = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {
            "engine": engine, "status": "FAILED", "error": "Worker created no result JSON", "jobs": []}
        record["worker_command"] = command
        record["worker_stdout"] = completed.stdout[-4000:]
        record["worker_stderr"] = completed.stderr[-4000:]
        record["worker_seconds"] = time.perf_counter() - launched
        if completed.returncode and record.get("status") == "SUCCESS":
            record.update(status="FAILED", error=f"Worker exit code {completed.returncode}")
        return record
    except Exception as exc:
        return {"engine": engine, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "jobs": [],
                "worker_command": command, "worker_seconds": time.perf_counter() - launched}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=ROOT / "standard_image.png")
    parser.add_argument("--easy-python", type=Path, default=DEFAULT_EASY)
    parser.add_argument("--paddle-python", type=Path, default=DEFAULT_PADDLE)
    args = parser.parse_args()
    if not args.image.is_file():
        raise SystemExit(f"Input image does not exist: {args.image}")
    settings = load_settings()
    ocr = OCREngine(settings)
    if ocr.error:
        raise SystemExit(f"Tesseract unavailable: {ocr.error}")
    output = create_batch_dir(Path(settings.output_dir) / "ocr_benchmark")
    original = load_image(args.image, settings)
    upright, orientation = OrientationDetector(settings, ocr).detect(original)
    if orientation["status"] not in RESOLVED:
        raise SystemExit(f"Automatic orientation unresolved: {orientation}")
    shared = output / "shared_upright.png"
    save_image(shared, upright)
    save_image(output / "original_input.png", original)
    if tuple(upright.shape[1::-1]) != (1131, 1600):
        raise SystemExit(f"Unexpected normalized dimensions {upright.shape[1::-1]}; crop coordinates need review.")
    crops_dir = output / "crops"
    crops_dir.mkdir()
    jobs = [{"id": "full", "path": str(shared), "offset": [0, 0], "kind": "full"}]
    crop_images = {}
    for crop_id, (x, y, w, h), _, _ in CROPS:
        crop = upright[y:y + h, x:x + w]
        path = crops_dir / f"{crop_id}.png"
        save_image(path, crop)
        crop_images[crop_id] = path
        jobs.append({"id": crop_id, "path": str(path), "offset": [x, y], "kind": "crop"})
    manifest = output / "manifest.json"
    write_json(manifest, {"jobs": jobs})
    source = {"path": str(args.image.resolve()), "sha256": hash_file(args.image),
              "original_dimensions": list(original.shape[1::-1]), "upright_dimensions": list(upright.shape[1::-1]),
              "orientation": orientation}
    tesseract_configs = {"Tesseract ara+eng PSM 3": (3, "full"), "Tesseract ara+eng PSM 11": (11, "full")}
    page_results, crop_results = {}, defaultdict(dict)
    for name, (psm, _) in tesseract_configs.items():
        tick = time.perf_counter()
        page = tesseract_result(ocr, upright, psm)
        page["inference_seconds"] = time.perf_counter() - tick
        page["configuration"] = {"language": settings.ocr_languages, "psm": psm, "oem": 1}
        page_results[name] = page
        for crop_id, _, _, crop_psm in CROPS:
            tick = time.perf_counter()
            # Full-page configurations are intentionally also shown on the same crops for an equal image comparison.
            result = tesseract_result(ocr, cv2.imread(str(crop_images[crop_id])), psm)
            result["inference_seconds"] = time.perf_counter() - tick
            result["configuration"] = {"language": settings.ocr_languages, "psm": psm, "oem": 1}
            crop_results[crop_id][name] = result
    crop_tess_name = "Tesseract ara+eng crop PSM 7/6"
    for crop_id, _, _, crop_psm in CROPS:
        psm = 7 if crop_psm == "psm7" else 6
        tick = time.perf_counter()
        result = tesseract_result(ocr, cv2.imread(str(crop_images[crop_id])), psm)
        result["inference_seconds"] = time.perf_counter() - tick
        result["configuration"] = {"language": settings.ocr_languages, "psm": psm, "oem": 1}
        crop_results[crop_id][crop_tess_name] = result
    external = {}
    for engine, interpreter in (("easyocr", args.easy_python), ("paddleocr", args.paddle_python)):
        destination = output / f"{engine}_worker.json"
        external[engine] = run_external(engine, interpreter, manifest, destination) if interpreter.is_file() else {
            "engine": engine, "status": "FAILED", "error": f"Interpreter missing: {interpreter}", "jobs": []}
        label = "EasyOCR ar+en CPU" if engine == "easyocr" else "PaddleOCR 3.x PP-OCRv5 Arabic CPU"
        by_id = {entry["id"]: entry for entry in external[engine].get("jobs", [])}
        for job in jobs:
            entry = by_id.get(job["id"], {"status": "FAILED", "regions": [], "error": "Worker returned no job"})
            regions = entry.get("regions", [])
            result = {"status": entry.get("status", external[engine].get("status", "FAILED")),
                      "error": entry.get("error", external[engine].get("error", "")), "regions": regions,
                      "text": text_from_regions(regions), "native": entry.get("native"),
                      "inference_seconds": entry.get("inference_seconds", 0),
                      "configuration": external[engine].get("model", {})}
            if job["id"] == "full":
                page_results[label] = result
            else:
                result["page_regions"] = [add_offset(region, job["offset"]) for region in regions]
                crop_results[job["id"]][label] = result
    # Overlay/images/text/JSON output for every displayed result.
    artifacts = {"original": output / "original_input.png", "upright": shared, "full_overlays": {}, "crop_overlays": defaultdict(dict)}
    all_results = {"full": page_results, "crops": crop_results}
    for track, collection in all_results.items():
        groups = {"full": collection} if track == "full" else collection
        for item_id, engines in groups.items():
            base = upright if item_id == "full" else cv2.imread(str(crop_images[item_id]))
            for name, result in engines.items():
                safe = "".join(c if c.isalnum() else "_" for c in name).strip("_")
                folder = output / track / item_id / safe
                folder.mkdir(parents=True, exist_ok=True)
                overlay_path = folder / "overlay.png"
                overlay(base, result["regions"], overlay_path)
                if item_id != "full":
                    offset = next(job["offset"] for job in jobs if job["id"] == item_id)
                    result["page_regions"] = [add_offset(region, offset) for region in result["regions"]]
                (folder / "text.txt").write_text(result.get("text", ""), encoding="utf-8")
                write_json(folder / "result.json", result)
                if item_id == "full":
                    artifacts["full_overlays"][name] = overlay_path
                else:
                    artifacts["crop_overlays"][item_id][name] = overlay_path
    evaluation = {}
    for crop_id, _, expected, _ in CROPS:
        evaluation[crop_id] = {name: crop_evaluation(expected, result["text"])
                               for name, result in crop_results[crop_id].items()}
    summary = {"created_at": datetime.now().astimezone().isoformat(), "input": source,
               "coordinator": {"python": sys.version, "platform": platform.platform(),
                               "tesseract": ocr.validation, "settings": {"ocr_languages": settings.ocr_languages,
                               "orientation": "automatic; external rotation/unwarping disabled"}},
               "crops": [{"id": cid, "bbox": list(box), "verified_ground_truth": expected, "tesseract_crop_mode": mode}
                         for cid, box, expected, mode in CROPS],
               "engines": {name: {"status": result["status"], "regions": len(result["regions"]),
                                   "characters": len(result["text"]), "inference_seconds": result["inference_seconds"],
                                   "configuration": result.get("configuration", {})}
                           for name, result in page_results.items()}}
    report = {"summary": summary, "full_page": page_results, "crop_results": crop_results,
              "external_workers": external, "evaluation": evaluation, "artifacts": {k: str(v) for k, v in artifacts.items() if k not in ("full_overlays", "crop_overlays")}}
    write_json(output / "benchmark.json", report)
    html_report(report, page_results, crop_results, artifacts, output / "report.html")
    print(output / "report.html")


if __name__ == "__main__":
    main()
