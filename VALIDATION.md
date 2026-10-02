# Verification record — 2026-09-22

Environment: Windows, existing Python **3.14.6**, PySide6 **6.11.2**, Tesseract **5.4.0.20240606**. No Python downgrade, new virtual environment or TensorFlow installation.

## Completed checks

- Syntax: `python -m compileall -q app.py config.py src gui tools tests` passed.
- Imports: every `src` and `gui` module imported successfully, including PySide6.
- Dependencies: `python -m pip check` reported no broken requirements.
- Tests: `python -m unittest discover -s tests -v` passed **16 tests**, including real Tesseract OCR with a language-data directory containing spaces. No tests require an exact OCR transcription.
- Tesseract diagnostic: `python app.py --diagnose` detected configured **ara + eng** in `D:\masters\code\tessdata`.
- GUI startup: `python app.py --smoke-test` passed using Qt's offscreen platform.
- Native Windows GUI: `tools/verify_gui.py` ran the real worker/OCR pipeline, processed three pages, verified completion, saved a screenshot and closed automatically. A 50 ms main-thread timer fired **706 times** during the run, confirming that the event loop continued processing while inspection ran.
- GUI tests also cover filters, every image tab, enabling/disabling controls, exported reports, cancellation and per-image errors.
- Error-path tests intentionally produce logged tracebacks for invalid input/write failures; these are expected handled cases, not failed tests.

## Real OCR sample results

Native GUI batch: `outputs/batch_20260922_135140_477e3679`.

| Sample | Status | Merged regions | SSIM | Text similarity |
|---|---|---:|---:|---:|
| printed_clean.png | PASS | 0 | 1.0000 | 1.0000 |
| printed_defective.png | DEFECTIVE | 25 | 0.9844 | 0.9482 |
| printed_misaligned.png | PASS | 0 | 0.9837 | 1.0000 |

All three inspections completed with Arabic/English OCR available. The defective image contains injected vertical/horizontal streaks, a dark blob and missing print; its reports contain 2 detected streaks and 4 OCR word differences. A missing region can affect several characters, so merged regions do not equal the number of injected editing operations. Red overlay PNGs preserve the 1200 × 1500 reference resolution.

Evidence: [native GUI screenshot](outputs/gui_smoke.png), [GUI smoke summary](outputs/gui_smoke.json), [batch JSON](outputs/batch_20260922_135140_477e3679/batch_report.json), [batch CSV](outputs/batch_20260922_135140_477e3679/batch_report.csv).

## Fixes made during verification

- Addressed pytesseract's Windows quoting of a `--tessdata-dir` argument using a scoped environment override, restored after each call and guarded by a lock.
- Separated stroke-density evidence from simple intensity differences to avoid labeling resampling of legitimate thin table lines as missing print. A regression test confirms alignment-only changes remain clean while a faded region is detected.
- Preserved UTF-8/Unicode paths and Arabic report text; retained OCR confidence and individual metrics.

These checks establish that the implemented workflow runs on this machine. They do not measure real-world defect sensitivity/specificity. No representative scanned-document dataset was supplied. Threshold calibration and academic evaluation remain dataset-dependent. Optional CNN/VGG16 is disabled; no learned defect model is included.
