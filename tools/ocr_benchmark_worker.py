"""External-engine worker for ``benchmark_ocr.py``.

Run this script with the dedicated EasyOCR or PaddleOCR interpreter, never the
application interpreter. It loads one model reader/pipeline and reuses it for
all page/crop jobs in the supplied manifest.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time


def serializable(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def find_result_mapping(value):
    """Find PaddleOCR v3's result payload without assuming a 2.x API."""
    if isinstance(value, dict):
        if "rec_texts" in value or "dt_polys" in value:
            return value
        for child in value.values():
            found = find_result_mapping(child)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_result_mapping(child)
            if found is not None:
                return found
    return None


def easy_reader():
    import easyocr
    reader = easyocr.Reader(["ar", "en"], gpu=False, verbose=False)
    return reader, {"package": "easyocr", "version": easyocr.__version__, "languages": ["ar", "en"], "gpu": False}


def paddle_reader():
    # PaddlePaddle 3.3 Windows can fail in the oneDNN executor for this model.
    # The benchmark is CPU-only; disable this optional acceleration path rather
    # than changing image pixels or switching to a different OCR engine/API.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    import paddle
    import paddleocr
    from paddleocr import PaddleOCR
    pipeline = PaddleOCR(
        lang="ar",
        ocr_version="PP-OCRv5",
        text_recognition_model_name="arabic_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        enable_mkldnn=False,
    )
    return pipeline, {
        "package": "paddleocr", "version": paddleocr.__version__,
        "paddlepaddle_version": paddle.__version__, "device": "cpu",
        "ocr_version": "PP-OCRv5", "recognition_model": "arabic_PP-OCRv5_mobile_rec",
        "use_doc_orientation_classify": False, "use_doc_unwarping": False,
        "use_textline_orientation": False, "enable_mkldnn": False,
    }


def run_easy(reader, path):
    rows = reader.readtext(str(path), detail=1, paragraph=False, rotation_info=None)
    regions = [{"bbox": [[float(x), float(y)] for x, y in quad], "text": str(text), "confidence": float(conf)}
               for quad, text, conf in rows]
    return regions, serializable(rows)


def run_paddle(pipeline, path):
    output = list(pipeline.predict(str(path)))
    native = []
    regions = []
    for result in output:
        candidate = getattr(result, "json", result)
        candidate = candidate() if callable(candidate) else candidate
        payload = serializable(candidate)
        native.append(payload)
        mapping = find_result_mapping(payload)
        if mapping is None:
            continue
        texts = mapping.get("rec_texts", []) or []
        scores = mapping.get("rec_scores", []) or []
        polygons = mapping.get("dt_polys", []) or []
        for index, text in enumerate(texts):
            box = polygons[index] if index < len(polygons) else []
            confidence = scores[index] if index < len(scores) else None
            regions.append({"bbox": serializable(box), "text": str(text), "confidence": confidence})
    return regions, native


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("easyocr", "paddleocr"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    started = time.perf_counter()
    report = {"engine": args.engine, "status": "FAILED", "python": sys.version,
              "platform": platform.platform(), "jobs": [], "warnings": []}
    try:
        reader, info = easy_reader() if args.engine == "easyocr" else paddle_reader()
        report["model"] = info
        report["initialization_seconds"] = time.perf_counter() - started
        for job in manifest["jobs"]:
            tick = time.perf_counter()
            try:
                regions, native = (run_easy(reader, job["path"]) if args.engine == "easyocr"
                                   else run_paddle(reader, job["path"]))
                report["jobs"].append({"id": job["id"], "status": "SUCCESS", "regions": regions,
                                       "native": native, "inference_seconds": time.perf_counter() - tick})
            except Exception as exc:
                report["jobs"].append({"id": job["id"], "status": "FAILED", "regions": [],
                                       "error": f"{type(exc).__name__}: {exc}",
                                       "inference_seconds": time.perf_counter() - tick})
        report["status"] = "SUCCESS"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
