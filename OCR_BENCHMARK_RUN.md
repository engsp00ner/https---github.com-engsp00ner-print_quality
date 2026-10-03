# Arabic OCR benchmark: setup and rerun

This benchmark is isolated from the application: the application virtual
environment runs the coordinator and Tesseract, while EasyOCR and PaddleOCR
use separate Python 3.11 virtual environments under the current user's home
directory. It does not change the application's selected OCR engine, inspection
pipeline, or source image pixels.

## Measured run

Input: `standard_image.png` (SHA-256
`08fda9ca784d969e9bb1a3f9bfb533e7a93e6cd22b62efad7f1fc71415e85d61`).
The automatic orientation detector applied a 180-degree clockwise rotation and
saved the shared lossless, upright `1131 x 1600` PNG. Every engine received
that exact file for page OCR and the same saved crop files for crop OCR.

Final successful run: `outputs/ocr_benchmark/batch_20261003_025033_58ce3f1b`.
The self-contained `report.html` puts each engine's unedited returned text
immediately below the corresponding overlay image.

The seven visually verified heading crops are scored after NFC and whitespace
normalization only. This is not full-page transcription accuracy. In that
limited crop evaluation, PaddleOCR had 2 exact, 5 incorrect, 0 missing and
mean CER 0.360; EasyOCR had 2 exact, 5 incorrect, 0 missing and mean CER
0.482. Tesseract PSM 3 had 0 exact, 2 incorrect, 5 missing (CER 0.761), PSM
11 had 0 exact, 7 incorrect, 0 missing (CER 0.793), and the crop-specific PSM
settings had 0 exact, 3 incorrect, 4 missing (CER 0.741).

Full-page inference time for the final run was approximately 0.27 seconds for
Tesseract PSM 3, 0.43 seconds for Tesseract PSM 11, 7.39 seconds for EasyOCR,
and 16.41 seconds for PaddleOCR. Model initialization and process launch times
are recorded separately in `benchmark.json`.

PaddleOCR uses its 3.x `PaddleOCR.predict` API with the
`arabic_PP-OCRv5_mobile_rec` recognizer, `device="cpu"`, document orientation
classification disabled, unwarping disabled, and text-line orientation disabled.
On Windows/PaddlePaddle 3.3, its optional oneDNN path failed for this model, so
the dedicated worker sets `FLAGS_use_mkldnn=0` / `enable_mkldnn=False`. The
successful result is still CPU-only and uses the same source pixels.

## Reproducible PowerShell setup

Run these commands from the project root. They create or update only the two
benchmark environments, not `print-defect`.

```powershell
$benchmarkPython = 'C:\Users\engsp00ner\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe'
$benchmarkRoot = Join-Path $env:USERPROFILE '.na3em_ocr_benchmark'

& $benchmarkPython -m venv (Join-Path $benchmarkRoot 'easy')
& (Join-Path $benchmarkRoot 'easy\Scripts\python.exe') -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
& (Join-Path $benchmarkRoot 'easy\Scripts\python.exe') -m pip install easyocr

& $benchmarkPython -m venv (Join-Path $benchmarkRoot 'paddle')
& (Join-Path $benchmarkRoot 'paddle\Scripts\python.exe') -m pip install paddlepaddle
& (Join-Path $benchmarkRoot 'paddle\Scripts\python.exe') -m pip install paddleocr
```

The measured environment used EasyOCR 1.7.2, Torch 2.14.1+cpu, PaddlePaddle
3.3.1, PaddleOCR 3.7.0, and PaddleX 3.7.2. EasyOCR downloads `craft_mlt_25k`
and `arabic` models at first use; PaddleOCR downloads official model artifacts
at first use.

## Rerun

```powershell
& 'F:\na3em_master\print-defect\Scripts\python.exe' tools\benchmark_ocr.py --image standard_image.png
```

The coordinator discovers the two dedicated environments at
`$env:USERPROFILE\.na3em_ocr_benchmark\easy` and `paddle`. Use
`--easy-python` and `--paddle-python` to supply different interpreter paths.
It creates a new timestamped directory under `outputs\ocr_benchmark`.

The crop coordinates are intentionally fixed for the normalized `1131 x 1600`
representative form. The tool rejects incompatible input dimensions rather than
silently scoring unrelated areas; use new verified shared crop coordinates for
a different form layout.

## References and limits

Installation/API/model decisions follow the official PaddleOCR 3.x
installation and OCR pipeline documentation, its PP-OCRv5 multilingual model
documentation, and the EasyOCR project README. The result is a useful candidate
comparison, not a production-engine switch: test a larger, labeled collection
of representative forms before changing application OCR. Empty table cells are
not evaluated, confidence values are not compared across engines, and no
external preprocessing (grid removal, sharpening, resizing, or contrast
changes) was applied to any engine in the primary run.
