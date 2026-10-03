"""Central settings. Defaults are starting points; calibrate using real scans."""
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    tesseract_path: str = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    tessdata_dir: str = os.environ.get("TESSDATA_PREFIX", "")
    ocr_languages: str = "ara+eng"
    ocr_enabled: bool = True
    ocr_psm: int = 3
    ocr_timeout: int = 90
    ocr_min_confidence: float = 45.0
    paddle_python: str = str(Path.home() / '.na3em_ocr_benchmark/paddle/Scripts/python.exe')
    paddle_model_dir: str = str(Path.home() / '.paddlex/official_models')
    paddle_min_confidence: float = 80.0
    auto_orientation: bool = True
    orientation_timeout: float = 30.0
    orientation_max_dimension: int = 2000
    orientation_osd_confidence: float = 15.0
    orientation_min_characters: int = 12
    orientation_min_confidence: float = 55.0
    orientation_min_margin: float = 0.20
    output_dir: str = str(ROOT / "outputs")
    supported_extensions: tuple = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
    max_image_pixels: int = 40_000_000
    ssim_threshold: float = 0.95
    text_similarity_threshold: float = 0.98
    extra_ink_threshold: float = 0.002
    missing_ink_threshold: float = 0.002
    min_defect_area: int = 40
    min_blob_area: int = 40
    blob_max_aspect: float = 3.0
    blob_min_fill: float = 0.5
    min_line_length: int = 100
    line_aspect_ratio: float = 8.0
    ink_threshold: int = 180
    intensity_difference: int = 35
    ink_density_difference: int = 80
    registration_tolerance: int = 1
    ssim_difference_threshold: float = 0.22
    component_close_size: int = 3
    orb_features: int = 6000
    alignment_max_dimension: int = 1800
    match_ratio: float = 0.75
    min_matches: int = 12
    min_inlier_ratio: float = 0.45
    ransac_threshold: float = 3.0
    min_alignment_correlation: float = 0.60
    min_coverage: float = 0.95
    max_corner_displacement: float = 0.35
    ecc_iterations: int = 80
    merge_iou: float = 0.25
    merge_containment: float = 0.65

    def __post_init__(self):
        if not 0 <= self.paddle_min_confidence <= 100:
            raise ValueError('paddle_min_confidence must be between 0 and 100')
        for name in ("ssim_threshold", "text_similarity_threshold", "extra_ink_threshold",
                     "missing_ink_threshold", "match_ratio", "min_inlier_ratio", "min_coverage",
                     "ssim_difference_threshold", "merge_iou", "merge_containment", "blob_min_fill",
                     "orientation_min_margin"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("min_defect_area", "min_blob_area", "min_line_length", "orb_features",
                     "alignment_max_dimension", "min_matches", "ocr_timeout", "max_image_pixels",
                     "component_close_size", "ecc_iterations", "orientation_timeout",
                     "orientation_max_dimension", "orientation_min_characters"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.orientation_min_confidence <= 100 or self.orientation_osd_confidence < 0:
            raise ValueError("Invalid orientation confidence threshold")
        if not 0 <= self.registration_tolerance <= 10:
            raise ValueError("registration_tolerance must be between 0 and 10")
        if any(not 0 <= getattr(self, name) <= 255 for name in ("ink_threshold", "intensity_difference", "ink_density_difference")):
            raise ValueError("Intensity thresholds must be between 0 and 255")

    def to_dict(self):
        return asdict(self)


def load_settings(path=None):
    path = Path(path or ROOT / "settings.json")
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - {field.name for field in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    return Settings(**data)
