# Orientation implementation and validation

Validated on 2026-10-03 using the project virtual environment and Tesseract
5.4.0.20240606. Tesseract was installed using the UB-Mannheim winget package;
its verified executable is `C:\Program Files\Tesseract-OCR\tesseract.exe`.
The local ignored `settings.json` now points to `F:\na3em_master\tessdata`.
Existing Arabic/English models were retained; the installed OSD model was copied
into that data directory. `app.py --diagnose` confirms `ara`, `eng`, and `osd`.

The original filename `standard_image.jpeg` was absent during implementation.
The current `standard_image1.jpeg` contains the same visible sideways form and
was used for live tests; `standard_image.png` contains the upside-down form.
Neither source was modified by the implementation.

## Results

- All four input rotations of the JPEG normalize to exactly the same upright pixels.
  The original JPEG requires 90 degrees clockwise; the current PNG requires 180.
- All 16 automatically detected reference/printed rotation pairs pass inspection
  with complete checks and no introduced defects. PNG fixtures isolate orientation
  from JPEG recompression.
- A direct comparison of the current JPEG and PNG also passes, with both directions
  independently resolved.
- The detector uses four-rotation OCR for this sparse form because OSD reports
  too few characters. Direction analysis takes approximately 1.90–2.07 seconds per
  rotated fixture. First comparisons (including reference preparation) take
  5.22–6.04 seconds; cached-reference comparisons take 2.65–3.52 seconds locally.
  These are measurements on this machine, not performance guarantees.
- Automated tests cover all 16 manual rotation pairs, coordinate transforms,
  EXIF handling, cache invalidation, persistent visual defects, ambiguous/blank
  results, OSD fallback, runtime state restoration, exhausted probe budgets,
  registration fallback uniqueness, and GUI controls/invalidation.
- Live mixed Arabic/English synthetic fixtures pass all four rotations. The
  existing live model-path test passes; no live tests were skipped.
- Additional live checks: blank and repeated-grid crops return `NO_TEXT_EVIDENCE`;
  a sparse Arabic heading crop resolves upright; a mixed-script page with 1-degree
  skew resolves upright through OSD; a clockwise-rotated English page correctly
  selects a 270-degree correction. These take about 0.68–1.95 seconds locally.
- The full 27-test suite passed before two additional targeted budget/registration
  tests were added. Both additional tests and the three GUI tests then passed.
  The GUI smoke test also passed.

Detailed live results, text/boxes, settings, model hashes, and timing:

`outputs/orientation_validation/batch_20261003_015016_64d55e01/validation.json`

Direct current-file comparison:

`outputs/batch_20261003_015339_35d229d3/page_standard_image_ea4ea1c8/report.json`

These generated outputs are local and git-ignored. Reproduce them with:

```powershell
.\print-defect\Scripts\python.exe app.py --diagnose
.\print-defect\Scripts\python.exe -m unittest discover -v
.\print-defect\Scripts\python.exe tools\verify_orientation.py standard_image1.jpeg --expected-correction 90 --all-pairs
```

## Remaining OCR/layout errors

Orientation is corrected, but the existing final OCR layout mode remains `ocr_psm=3`.
It misses most headings on this table. A PASS on identical pages means their
recognized text and visual checks agree; it does not mean the transcription is
complete or correct.

| Extraction | Recognized words | Mean OCR confidence |
| --- | ---: | ---: |
| Original sideways pixels, PSM 3 | 11 | 32.55 |
| Upright pixels, PSM 3 | 7 | 76.14 |
| Upright pixels, PSM 11 diagnostic | 39 | 60.38 |

Confidence is not accuracy. The higher upright PSM 3 confidence accompanies
substantial omissions. Its text is approximately `(TLE ١١6 إستمارة رقم (` and
`الباقس بالعههدة`. The original sideways extraction is largely unrelated text.

Independent visual reading of seven clear headings gives:

1. دفتر عهدة المخزن
2. رقم الصنف
3. إسم الصنف
4. الوحدة
5. الباقي بالعهدة
6. أو منصرف إلى
7. ملاحظات

Neither PSM 3 extraction reproduces these seven phrases exactly. The upright
sparse diagnostic reproduces four: رقم الصنف, إسم الصنف, الوحدة, أو منصرف إلى.
It misreads the title as `دفئر عهدة المشسزن`, the balance heading as
`الباقس بالعههدة`, and notes as `ملاحضات`. This is a focused heading check, not
a full-page character error rate. Numeric form metadata and distorted small
headings were excluded from this manual sample.

The sparse diagnostic is evidence of a separate table-layout/Arabic recognition
problem, not a default change to final OCR. The current work implements the
orientation plan; further recognition improvements need table/cell segmentation
and model evaluation against more manually verified scans.

## Operational limits

The default thresholds are conservative starting points verified on these fixtures,
not universal calibration. Ambiguous evidence remains review-required, with manual
clockwise choices available through **Text direction...**. Mixed directions within
one page, mirrored pages, and universal Arabic transcription accuracy are unsupported.
OSD and OCR subprocess calls share a time budget; native registration/image work may
finish its current operation after the deadline, but partial candidate sets cannot win.

Direction correction is exact quarter-turn pixel rearrangement. No grid erasure,
image enhancement, or additional resampling is applied to inspection pixels.
Existing registration and defect thresholds remain in effect.
