"""Persistent isolated Paddle 3.x process. No model imports on the GUI thread."""
import atexit
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import cv2
from .models import OCRResult, OCRWord

CONFIG = dict(engine="paddleocr", version="3.7.0", paddlepaddle="3.3.1", paddlex="3.7.2",
              ocr_version="PP-OCRv5", recognition_model="arabic_PP-OCRv5_mobile_rec",
              detection_model="PP-OCRv6_medium_det", device="cpu", enable_mkldnn=False,
              FLAGS_use_mkldnn="0", use_doc_orientation_classify=False,
              use_doc_unwarping=False, use_textline_orientation=False)
_clients = {}
_lock = threading.Lock()


def fingerprint(settings):
    root = Path(settings.paddle_model_dir)
    files = [root / model / name for model in (CONFIG['detection_model'], CONFIG['recognition_model'])
             for name in ('inference.json', 'inference.pdiparams', 'inference.yml')]
    return (json.dumps(CONFIG, sort_keys=True), settings.paddle_python, str(root),
            tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else (str(p), None) for p in files))


class Client:
    def __init__(self, settings):
        if not Path(settings.paddle_python).is_file():
            raise RuntimeError(f"PaddleOCR interpreter missing: {settings.paddle_python}. See PADDLE_ROTATION_REPORT.md.")
        self.storage = tempfile.TemporaryDirectory(prefix="na3em_paddle_", ignore_cleanup_errors=True)
        self.root = Path(self.storage.name)
        self.log = (self.root / 'runtime.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([settings.paddle_python, str(Path(__file__).with_name('paddle_worker.py')),
                                         settings.paddle_model_dir], stdin=subprocess.PIPE,
                                        stdout=self.log, stderr=self.log, text=True, encoding='utf-8',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        atexit.register(self.close)

    def close(self):
        atexit.unregister(self.close)
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdin.close()
        self.log.close()
        self.storage.cleanup()

    def predict(self, image, timeout):
        source, target = self.root / 'input.png', self.root / 'result.json'
        target.unlink(missing_ok=True)
        source.write_bytes(cv2.imencode('.png', image)[1].tobytes())
        self.process.stdin.write(json.dumps([str(source), str(target)]) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while not target.exists():
            if self.process.poll() is not None:
                raise RuntimeError('PaddleOCR process exited: ' + (self.root / 'runtime.log').read_text(encoding='utf-8', errors='replace')[-2500:])
            if time.monotonic() > deadline:
                raise RuntimeError(f'PaddleOCR exceeded {timeout}s; worker terminated. No fallback was used.')
            time.sleep(.02)
        data = json.loads(target.read_text(encoding='utf-8'))
        if data.get('error'):
            raise RuntimeError(data['error'])
        return data


def extract(image, settings):
    metadata = {**CONFIG, 'model_dir': settings.paddle_model_dir, 'granularity': 'line',
                'confidence_scale': '0..100 (native score multiplied by 100)'}
    try:
        with _lock:
            key = fingerprint(settings)
            try:
                if key not in _clients:
                    _clients[key] = Client(settings)
                data = _clients[key].predict(image, settings.ocr_timeout)
            except Exception:
                client = _clients.pop(key, None)
                if client:
                    client.close()
                raise
        words = []
        for i, region in enumerate(data['regions']):
            polygon = region['bbox']
            xs, ys = zip(*polygon)
            x, y = int(min(xs)), int(min(ys))
            box = (x, y, max(1, int(max(xs)) - x), max(1, int(max(ys)) - y))
            words.append(OCRWord(region['text'], float(region['confidence']) * 100, box,
                                 (1, 1, 1, i + 1), polygon, 'line'))
        strict = '\n'.join(w.text for w in words)
        metadata.update(data['coverage'])
        return OCRResult(strict, ' '.join(strict.split()), words,
                         sum(w.confidence for w in words) / len(words) if words else None,
                         'SUCCESS', metadata=metadata)
    except Exception as exc:
        logging.getLogger(__name__).warning('PaddleOCR failed: %s', exc)
        return OCRResult(status='FAILED', error=f'PaddleOCR: {exc}', metadata=metadata)


def close_clients():
    for client in list(_clients.values()):
        client.close()
    _clients.clear()
