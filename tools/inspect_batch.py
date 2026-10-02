"""Command-line batch runner using the same engine as the GUI."""
import argparse
import logging
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_settings
from src.image_loader import discover_images
from src.inspection_engine import InspectionEngine
from src.reporting import create_batch_dir, write_batch_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--images", nargs="+", type=Path)
    parser.add_argument("--folder", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    paths = args.images or (discover_images(args.folder, settings) if args.folder else [])
    if not paths:
        parser.error("Supply --images or a non-empty --folder")
    started = time.perf_counter()
    folder = create_batch_dir(settings.output_dir)
    engine = InspectionEngine(settings)
    results = [engine.inspect(args.reference, path, folder) for path in paths]
    write_batch_report(folder, results, settings, time.perf_counter() - started)
    print(folder.resolve())
    for result in results:
        print(f"{result.filename}: {result.status}, {len(result.defects)} regions, complete={result.inspection_complete}")
    return 0 if all(r.inspection_complete for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
