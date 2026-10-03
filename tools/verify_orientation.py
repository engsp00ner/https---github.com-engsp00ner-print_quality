"""Live, reproducible orientation/OCR diagnostic. Does not alter input images."""
import argparse
from dataclasses import asdict
import hashlib
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from config import load_settings
from src.image_loader import load_image, save_image
from src.inspection_engine import InspectionEngine
from src.ocr_engine import OrientationOCR as OCREngine
from src.orientation import ANGLES, RESOLVED, OrientationDetector, rotate_page
from src.reporting import create_batch_dir, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--expected-correction", type=int, choices=ANGLES, required=True)
    parser.add_argument("--all-pairs", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    ocr = OCREngine(settings)
    if ocr.error:
        raise SystemExit(ocr.error)
    folder = create_batch_dir(Path(settings.output_dir) / "orientation_validation")
    image = load_image(args.image, settings)
    upright = rotate_page(image, args.expected_correction)
    report = {"image": str(args.image.resolve()), "sha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
              "settings": settings.to_dict(), "runtime": ocr.validation,
              "version": subprocess.check_output([ocr.validation["executable"], "--version"], text=True),
              "models": {}, "baseline": asdict(ocr.extract(image)),
              "upright": asdict(ocr.extract(upright)),
              "upright_sparse_diagnostic": asdict(ocr.extract(upright, psm=11)), "rotations": [], "pairs": []}
    for name in settings.ocr_languages.split("+") + ["osd"]:
        path = Path(settings.tessdata_dir) / f"{name}.traineddata"
        if path.exists():
            report["models"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    detector = OrientationDetector(settings, ocr)
    paths = {}
    success = True
    for angle in ANGLES:
        pixels = rotate_page(upright, angle)
        normalized, info = detector.detect(pixels)
        ok = info["status"] in RESOLVED and np.array_equal(normalized, upright)
        success &= ok
        report["rotations"].append({"input_clockwise_from_upright": angle, "passed": bool(ok), **info})
        paths[angle] = folder / f"input_{angle}.png"
        save_image(paths[angle], pixels)
        print(f"Rotation {angle}: {info['status']} correction={info['correction_clockwise']} passed={ok}", flush=True)
    if args.all_pairs:
        for reference_angle, reference in paths.items():
            engine = InspectionEngine(settings)
            for angle, path in paths.items():
                started = time.perf_counter()
                result = engine.inspect(reference, path, folder)
                ok = result.status == "PASS" and result.inspection_complete
                success &= ok
                report["pairs"].append({"reference_angle": reference_angle, "printed_angle": angle,
                                         "passed": bool(ok), "elapsed": time.perf_counter() - started,
                                         "result": result.to_dict()})
                print(f"Pair {reference_angle}/{angle}: {result.status} complete={result.inspection_complete}", flush=True)
    report["passed"] = bool(success)
    write_json(folder / "validation.json", report)
    print(folder / "validation.json", flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
