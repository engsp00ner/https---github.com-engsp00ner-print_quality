"""Generate reproducible documents and controlled defects for inspection tests."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from src.image_loader import load_image, save_image


def create_reference(width=1200, height=1500):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    fonts = [Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font_path = next((path for path in fonts if path.exists()), None)
    font = ImageFont.truetype(str(font_path), 30) if font_path else ImageFont.load_default(size=30)
    title = ImageFont.truetype(str(font_path), 40) if font_path else font
    draw.text((70, 60), "PRINT QUALITY CONTROL", font=title, fill="black")
    draw.text((70, 135), "Order No. 12584", font=font, fill="black")
    draw.text((700, 135), "وزارة الدفاع", font=font, fill="black")
    draw.line((70, 205, width - 70, 205), fill="black", width=3)
    for i in range(12):
        y = 245 + i * 70
        draw.text((70, y), f"Item {i+1:02d}    Document verification    Quantity {i*7+13:03d}", font=font, fill="black")
    draw.rectangle((70, 1130, 1100, 1370), outline="black", width=3)
    for x in (380, 760):
        draw.line((x, 1130, x, 1370), fill="black", width=2)
    for y in (1210, 1290):
        draw.line((70, y, 1100, y), fill="black", width=2)
    draw.text((95, 1150), "Reference", font=font, fill="black")
    draw.text((400, 1230), "Approved", font=font, fill="black")
    draw.text((790, 1310), "2026 / 12584", font=font, fill="black")
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def apply_defects(image, kinds, angle=1.0, shift=6):
    result = image.copy()
    h, w = image.shape[:2]
    if "vertical" in kinds:
        cv2.line(result, (int(w * .89), int(h * .20)), (int(w * .89), int(h * .70)), (0, 0, 0), max(3, w // 250))
    if "horizontal" in kinds:
        cv2.line(result, (int(w * .15), int(h * .72)), (int(w * .75), int(h * .72)), (0, 0, 0), max(3, h // 300))
    if "blob" in kinds:
        cv2.circle(result, (int(w * .80), int(h * .34)), max(8, w // 55), (0, 0, 0), -1)
    if "missing" in kinds:
        cv2.rectangle(result, (int(w * .17), int(h * .22)), (int(w * .35), int(h * .29)), (255, 255, 255), -1)
    if "blur" in kinds:
        result = cv2.GaussianBlur(result, (9, 9), 2.2)
    if "rotation" in kinds or "shift" in kinds:
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle if "rotation" in kinds else 0, 1)
        if "shift" in kinds:
            matrix[:, 2] += shift
        result = cv2.warpAffine(result, matrix, (w, h), borderValue=(255, 255, 255))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("samples/printed_defective.png"))
    parser.add_argument("--defects", nargs="+", choices=["vertical", "horizontal", "blob", "missing", "blur", "rotation", "shift"], default=["vertical", "horizontal", "blob", "missing"])
    parser.add_argument("--angle", type=float, default=1.0)
    parser.add_argument("--shift", type=int, default=6)
    parser.add_argument("--demo", action="store_true", help="Create clean, defective and misaligned samples")
    args = parser.parse_args()
    if args.demo:
        root = Path(__file__).resolve().parents[1] / "samples"
        reference = create_reference()
        save_image(root / "standard.png", reference)
        save_image(root / "printed_clean.png", reference)
        save_image(root / "printed_defective.png", apply_defects(reference, ["vertical", "horizontal", "blob", "missing"]))
        save_image(root / "printed_misaligned.png", apply_defects(reference, ["rotation", "shift"]))
        print(f"Created demonstration images in {root}")
    else:
        image = load_image(args.input) if args.input else create_reference()
        save_image(args.output, apply_defects(image, args.defects, args.angle, args.shift))
        print(args.output.resolve())


if __name__ == "__main__":
    main()
