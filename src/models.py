from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Defect:
    type: str
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def bbox(self):
        return (self.x, self.y, self.width, self.height)


@dataclass
class OCRWord:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    line_id: tuple[int, int, int, int]


@dataclass
class OCRResult:
    strict_text: str = ""
    diagnostic_text: str = ""
    words: list[OCRWord] = field(default_factory=list)
    confidence: float | None = None
    status: str = "UNAVAILABLE"
    error: str = ""


@dataclass
class InspectionResult:
    filename: str
    printed_path: str
    reference_path: str
    status: str = "DEFECTIVE"
    inspection_complete: bool = False
    alignment: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    defects: list[Defect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    decision_reasons: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    ocr: dict = field(default_factory=dict)
    output_dir: str = ""
    processing_time_seconds: float = 0.0

    def to_dict(self):
        data = asdict(self)
        for defect in data["defects"]:
            defect["bbox"] = [defect[k] for k in ("x", "y", "width", "height")]
        return data
