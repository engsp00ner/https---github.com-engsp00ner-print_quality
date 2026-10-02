import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
import pytesseract
from .models import OCRResult, OCRWord
from .preprocessing import for_ocr

log = logging.getLogger(__name__)
_OCR_LOCK = threading.Lock()


def validate_tesseract(settings):
    executable = str(Path(settings.tesseract_path))
    if not Path(executable).is_file():
        executable = shutil.which(settings.tesseract_path) or ""
    if not executable:
        raise RuntimeError(f"Tesseract executable not found: {settings.tesseract_path}. Install Tesseract and set tesseract_path in settings.json.")
    command = [executable, "--list-langs"]
    if settings.tessdata_dir:
        command += ["--tessdata-dir", settings.tessdata_dir]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=settings.ocr_timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise RuntimeError(f"Tesseract validation failed: {completed.stderr.strip()}")
    languages = [line.strip() for line in completed.stdout.splitlines() if re.fullmatch(r"[\w/]+", line.strip())]
    missing = set(settings.ocr_languages.split("+")) - set(languages)
    if missing:
        names = ", ".join(f"{language}.traineddata" for language in sorted(missing))
        raise RuntimeError(f"Missing Tesseract language data: {names}. Install these files in the configured tessdata directory.")
    pytesseract.pytesseract.tesseract_cmd = executable
    return {"executable": executable, "languages": languages, "requested": settings.ocr_languages}


class OCREngine:
    def __init__(self, settings):
        self.settings = settings
        self.error = ""
        self.validation = {}
        if not settings.ocr_enabled:
            self.error = "OCR explicitly disabled; content inspection is incomplete."
        else:
            try:
                self.validation = validate_tesseract(settings)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                log.warning(self.error)

    def extract(self, image):
        if self.error:
            return OCRResult(error=self.error)
        try:
            options = f"--oem 1 --psm {self.settings.ocr_psm}"
            # pytesseract 0.3.13 uses non-POSIX shlex on Windows, retaining quotes in
            # --tessdata-dir arguments. A scoped environment override supports spaces
            # and Unicode paths without injecting literal quotes into the executable.
            with _OCR_LOCK:
                previous = os.environ.get("TESSDATA_PREFIX")
                previous_command = pytesseract.pytesseract.tesseract_cmd
                try:
                    if self.settings.tessdata_dir:
                        os.environ["TESSDATA_PREFIX"] = self.settings.tessdata_dir
                    pytesseract.pytesseract.tesseract_cmd = self.validation["executable"]
                    data = pytesseract.image_to_data(for_ocr(image), lang=self.settings.ocr_languages,
                                                     config=options, output_type=pytesseract.Output.DICT,
                                                     timeout=self.settings.ocr_timeout)
                finally:
                    pytesseract.pytesseract.tesseract_cmd = previous_command
                    if previous is None:
                        os.environ.pop("TESSDATA_PREFIX", None)
                    else:
                        os.environ["TESSDATA_PREFIX"] = previous
            words = []
            lines = {}
            for i, text in enumerate(data["text"]):
                text = text.strip()
                if not text:
                    continue
                line = tuple(int(data[k][i]) for k in ("page_num", "block_num", "par_num", "line_num"))
                bbox = tuple(int(data[k][i]) for k in ("left", "top", "width", "height"))
                words.append(OCRWord(text, float(data["conf"][i]), bbox, line))
                lines.setdefault(line, []).append(text)
            # Retain Tesseract logical order, including RTL/mixed lines. Never sort by x.
            strict = "\n".join(" ".join(line) for line in lines.values())
            return OCRResult(strict, " ".join(strict.split()), words,
                             sum(w.confidence for w in words) / len(words) if words else None, "SUCCESS")
        except (RuntimeError, OSError, pytesseract.TesseractError) as exc:
            log.warning("OCR failed: %s", exc)
            return OCRResult(status="FAILED", error=str(exc))
