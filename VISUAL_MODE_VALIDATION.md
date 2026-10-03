# Visual inspection and optional OCR validation

Compared `standard_image.png` with all 54 PNGs in `defalt/` using the updated engine, OCR off, automatic feature-based rotation.

- Median processing time: **2.49 seconds/page**; sum of per-page times: **137.1 seconds**, including saved image artifacts. This is a local measurement, not a controlled before/after speed ratio.
- All 52 nonblank pages reached the visual detectors. 39 completed all required visual checks; 13 retained their detected regions but require review for reference coverage below 95%.
- Nearly blank pages 52 and 54 failed registration and require review; no pixel defects were invented for them.
- OCR stages are explicitly SKIPPED and not required in visual-only mode. Enabled OCR still participates in completion and review decisions.

## Evidence

- [Batch report](outputs/batch_20261003_235034_96c3004b/batch_report.json)
- [CSV report](outputs/batch_20261003_235034_96c3004b/batch_report.csv)
- [Representative images, extra-ink masks and overlays](outputs/visual_mode_examples.png)
- [Setup window with OCR toggle](outputs/ocr_toggle_ui.png)

## Regression checks

- Full suite: 48 tests, 47 passed and 1 optional live OCR orientation test skipped because OCR is disabled in local settings.
- Added full-page archive round-trip afterward; reran all nine GUI/archive tests successfully, including full-page markers. Reran the threaded UI/engine/archive check after updating coordinate metadata; passed.
- Tests cover all four rotation angles without OCR, no OCR validation or extraction in disabled mode, all four corner marks beyond the reference canvas, pale margin ink, isolated-noise rejection, and retained ink evidence with insufficient coverage.
- Toggle persistence, save failure rollback, and disabling the control while busy are verified.

## Limits

Full-page margins outside the reference use white background. Use a full-page reference on white paper; cropped references and colored paper need review. These scans have no manually labeled ground truth, so this run does not establish that every defect is found or that every marked region is a true defect. Grid and resampling differences can still produce extra findings.

Review pages: page_0001.png, page_0002.png, page_0004.png, page_0011.png, page_0012.png, page_0013.png, page_0015.png, page_0019.png, page_0020.png, page_0038.png, page_0039.png, page_0044.png, page_0050.png, page_0052.png, page_0054.png.
