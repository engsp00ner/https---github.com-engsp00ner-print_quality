"""Render PDF pages to PNG. Install: python -m pip install pymupdf"""

import argparse
from pathlib import Path
import sys

import pymupdf


def convert(pdf_path: Path, dpi: int = 200, password: str = "", output_dir: Path = None) -> Path:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise ValueError(f"File not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Please select a PDF file.")
    if dpi <= 0:
        raise ValueError("DPI must be greater than zero.")

    output_dir = Path(output_dir).expanduser().resolve() if output_dir else pdf_path.with_suffix("")
    with pymupdf.open(pdf_path) as document:
        if document.needs_pass and not document.authenticate(password):
            raise ValueError("PDF is password protected. Use --password.")
        output_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(document, start=1):
            output = output_dir / f"page_{index:04d}.png"
            if output.exists():
                raise FileExistsError(f"Image already exists: {output}")
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            pixmap.save(output)
            print(f"[{index}/{len(document)}] {output.name}")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert each PDF page to a PNG image.")
    parser.add_argument("pdf", type=Path, help="Path to the PDF file")
    parser.add_argument("--dpi", type=int, default=200, help="Image resolution (default: 200)")
    parser.add_argument("--password", default="", help="Password for encrypted PDFs")
    args = parser.parse_args()
    try:
        folder = convert(args.pdf, args.dpi, args.password)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Done. Images saved to: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
