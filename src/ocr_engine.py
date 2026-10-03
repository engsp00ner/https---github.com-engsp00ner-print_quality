import logging
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
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
    return {"executable": executable, "languages": languages, "requested": settings.ocr_languages}


class OrientationOCR:
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

    @contextmanager
    def runtime(self, timeout):
        """Serialize all OCR/OSD calls and include lock waiting in their budget."""
        if self.error:
            raise RuntimeError(self.error)
        started = time.monotonic()
        if not _OCR_LOCK.acquire(timeout=max(0.0, timeout)):
            raise RuntimeError("OCR runtime busy; time budget exhausted")
        previous = os.environ.get("TESSDATA_PREFIX")
        previous_command = pytesseract.pytesseract.tesseract_cmd
        try:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError("OCR time budget exhausted")
            if self.settings.tessdata_dir:
                os.environ["TESSDATA_PREFIX"] = self.settings.tessdata_dir
            pytesseract.pytesseract.tesseract_cmd = self.validation["executable"]
            yield remaining
        finally:
            pytesseract.pytesseract.tesseract_cmd = previous_command
            if previous is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = previous
            _OCR_LOCK.release()

    def orientation_probe(self, image, timeout):
        with self.runtime(timeout) as remaining:
            return pytesseract.image_to_osd(for_ocr(image), output_type=pytesseract.Output.DICT,
                                            timeout=remaining)

    def extract(self, image, *, psm=None, timeout=None):
        """Recognize pixels as supplied; page normalization belongs to inspection."""
        if self.error:
            return OCRResult(error=self.error)
        try:
            options = f"--oem 1 --psm {self.settings.ocr_psm if psm is None else psm}"
            # pytesseract 0.3.13 uses non-POSIX shlex on Windows, retaining quotes in
            # --tessdata-dir arguments. A scoped environment override supports spaces
            # and Unicode paths without injecting literal quotes into the executable.
            with self.runtime(self.settings.ocr_timeout if timeout is None else timeout) as remaining:
                data = pytesseract.image_to_data(for_ocr(image), lang=self.settings.ocr_languages,
                                                 config=options, output_type=pytesseract.Output.DICT,
                                                 timeout=remaining)
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


class OCREngine:
    """Final recognition only. OrientationOCR owns the separate Tesseract dependency."""

    def __init__(self, settings):
        self.settings = settings
        self.error = '' if settings.ocr_enabled else 'OCR explicitly disabled'

    def extract(self, image):
        if not self.settings.ocr_enabled:
            return OCRResult(status="UNAVAILABLE", error="OCR explicitly disabled; content inspection is incomplete.")
        from .paddle_runtime import extract
        return extract(image, self.settings)
