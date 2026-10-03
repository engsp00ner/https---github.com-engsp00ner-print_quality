"""Run exclusively under the benchmark's pinned Paddle Python environment."""
import json
import os
from pathlib import Path
import sys

os.environ['FLAGS_use_mkldnn'] = '0'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'


def create_pipeline(root):
    from importlib.metadata import version
    for package, expected in [('paddleocr', '3.7.0'), ('paddlepaddle', '3.3.1'), ('paddlex', '3.7.2')]:
        if version(package) != expected:
            raise RuntimeError(f'{package} must be {expected}; found {version(package)}')
    # 3.7.0 resolves lang=ar + PP-OCRv5 to this detector in the recorded benchmark.
    det, rec = 'PP-OCRv6_medium_det', 'arabic_PP-OCRv5_mobile_rec'
    for model in (det, rec):
        for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            if not (root / model / name).is_file():
                raise RuntimeError(f'Missing local PaddleOCR model file: {root / model / name}. Install benchmark models before offline use.')
    from paddleocr import PaddleOCR
    return PaddleOCR(lang='ar', ocr_version='PP-OCRv5', device='cpu',
                     text_detection_model_name=det, text_detection_model_dir=str(root / det),
                     text_recognition_model_name=rec, text_recognition_model_dir=str(root / rec),
                     use_doc_orientation_classify=False, use_doc_unwarping=False,
                     use_textline_orientation=False, enable_mkldnn=False)


def predict(pipeline, path):
    regions, detected = [], 0
    for result in pipeline.predict(path):
        payload = result.json
        payload = payload() if callable(payload) else payload
        mapping = payload.get('res', payload)
        texts, scores = mapping['rec_texts'], mapping['rec_scores']
        # rec_polys follows recognition filtering; dt_polys may contain rejected lines.
        polygons = mapping['rec_polys']
        if not len(texts) == len(scores) == len(polygons):
            raise RuntimeError('Inconsistent PaddleOCR text/score/polygon lengths')
        detected += len(mapping['dt_polys'])
        regions.extend(dict(text=text, confidence=float(score), bbox=polygon)
                       for text, score, polygon in zip(texts, scores, polygons))
    return dict(regions=regions, coverage=dict(detected_regions=detected, recognized_regions=len(regions),
                coverage_verified=False, rejected_regions=detected - len(regions)))


if __name__ == '__main__':
    pipeline = None
    for line in sys.stdin:
        source, target = json.loads(line)
        try:
            if pipeline is None:
                pipeline = create_pipeline(Path(sys.argv[1]))
            data = predict(pipeline, source)
        except Exception as exc:
            data = {'error': f'{type(exc).__name__}: {exc}'}
        target = Path(target)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temporary.replace(target)
