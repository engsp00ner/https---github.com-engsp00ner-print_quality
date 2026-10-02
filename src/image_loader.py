from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from config import Settings


def load_image(path, settings=None):
    settings = settings or Settings()
    path = Path(path)
    if path.suffix.lower() not in settings.supported_extensions:
        raise ValueError(f"Unsupported image format: {path.suffix}")
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    try:
        with Image.open(path) as image:
            if image.width * image.height > settings.max_image_pixels:
                raise ValueError(f"Image exceeds {settings.max_image_pixels:,} pixels: {path.name}")
            if min(image.size) < 16:
                raise ValueError("Images must be at least 16 × 16 pixels")
            if getattr(image, "n_frames", 1) > 1:
                raise ValueError("Multi-page images must be exported as separate page images first")
            image = ImageOps.exif_transpose(image).convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            background.alpha_composite(image)
            return cv2.cvtColor(np.asarray(background.convert("RGB")), cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot read image {path}: {exc}") from exc


def save_image(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise OSError(f"Could not encode {path}")
    encoded.tofile(str(path))  # Unicode Windows paths


def discover_images(folder, settings=None):
    settings = settings or Settings()
    return sorted((p.resolve() for p in Path(folder).iterdir()
                   if p.is_file() and p.suffix.lower() in settings.supported_extensions),
                  key=lambda p: p.name.casefold())
