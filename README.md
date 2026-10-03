# Print Defect Inspection System

A Windows/PySide6 desktop prototype that compares a batch of printed pages with one correct reference. It combines alignment, Arabic/English OCR, strict text comparison, SSIM, extra/missing ink analysis, streaks and blob detection. The default result is the **printed page, aligned to the reference, with red defect rectangles**. Processing runs in a worker thread; results appear as each page finishes.

## Launch on this machine

```powershell
cd D:\masters\code
.\print-defect\Scripts\Activate.ps1
python app.py
```

Activation is optional:

```powershell
cd D:\masters\code
.\print-defect\Scripts\python.exe app.py
```

If PowerShell blocks activation, change the policy for this terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\print-defect\Scripts\Activate.ps1
```

The existing Python 3.14.6 environment is retained. PySide6 is the only newly required Python package; TensorFlow is neither imported nor required. For a missing GUI dependency:

```powershell
.\print-defect\Scripts\python.exe -m pip install PySide6==6.11.2
```

To install all declared dependencies in the existing environment:

```powershell
pip install -r requirements.txt
```

Requirements: Windows 11, Python 3.14, packages in `requirements.txt`, and Tesseract 5 with `ara` and `eng` language data. [Qt's installation documentation](https://doc.qt.io/qtforpython-6/) describes the PySide6 distribution.

## Tesseract and Arabic

`pytesseract` is a wrapper; the Tesseract executable is a separate dependency. The default executable path is configured once in `config.py`: `C:\Program Files\Tesseract-OCR\tesseract.exe`. Override it in **Settings**, `settings.json`, or the `TESSERACT_CMD` environment variable. Set `tessdata_dir` to this checkout's absolute `tessdata` directory; the local settings file is machine-specific and git-ignored.

Install the Windows engine if necessary:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact --source winget --accept-package-agreements --accept-source-agreements
```

The Windows installer is linked from [Tesseract's installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html). Arabic requires `ara.traineddata`; English requires `eng.traineddata`. The application validates **both** before attempting OCR. Missing files produce explicit warnings and prevent a PASS result. The default installer may contain English without Arabic.

To provision local language data without changing Program Files:

```powershell
New-Item -ItemType Directory -Force .\tessdata
Copy-Item 'C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata' .\tessdata\eng.traineddata
Invoke-WebRequest 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/ara.traineddata' -OutFile .\tessdata\ara.traineddata
```

Set `tessdata_dir` to the absolute project `tessdata` path in Settings. The Arabic model is from the [official tessdata_fast repository](https://github.com/tesseract-ocr/tessdata_fast), whose LSTM models use OEM 1. Alternatively put both language files in `C:\Program Files\Tesseract-OCR\tessdata` and leave `tessdata_dir` empty.

Diagnostics (PowerShell needs the `&` call operator before a quoted executable):

```powershell
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --tessdata-dir 'D:\masters\code\tessdata' --list-langs
.\print-defect\Scripts\python.exe app.py --diagnose
```

The first command reports system language files; the second and application diagnostic use the project-local data. The configured default is `ara+eng`. Changing that list changes the languages being inspected.

## Desktop workflow

1. Click **Select Reference Image** and select the correct page. Its filename, full path and preview appear.
2. Click **Select Images** for one/many printed pages, or **Select Folder** for all supported images directly inside a folder. Each selection replaces the previous printed list; folder loading is not recursive.
3. To convert a PDF into page images, use **PDF TO PNG**. Select the PDF, review or change the output directory, and click **Extract pages**. The default is a sibling folder named after the PDF without its extension.
4. Click **START INSPECTION**. The same cached reference is reused throughout the batch.
4. Inspect the progressive results, summary and All/PASS/DEFECTIVE filter. **Cancel after current image** saves completed work and stops safely between images.
5. Select a result row. The default **Defect Overlay** shows red rectangles. Other tabs provide Original Printed, Aligned Image, Difference Mask, Extra Ink, Missing Ink, Detected Lines and OCR Comparison.
6. Use Fit, +/−, 100%, mouse wheel zoom and drag to pan. The original page is saved at full resolution; large GUI previews are reduced to bound display memory. At 100%, preview pixels are scaled to the original pixel coordinates.
7. Read details for alignment statistics, OCR confidence, expected/detected text, defect coordinates, metrics and decision reasons. Arabic uses Qt's Unicode/bidirectional text rendering.
8. Use **Open Results Folder** or **Export CSV**. CSV export includes all processed results, irrespective of the current table filter.

Supported files: JPG/JPEG, PNG, BMP, TIF/TIFF, including Unicode paths. Transparency is composited onto white; EXIF orientation is applied. Multipage TIFFs must be split into one image per page. A configurable 40-million-pixel limit bounds per-page memory.

## Pipeline and decisions

`InspectionEngine.inspect(reference_path, printed_path, batch_dir=None)` is the high-level API. No Qt dependencies exist in the backend. A reusable engine caches reference grayscale, ink mask, ORB features and OCR until its path/size/modification timestamp changes. Keep one engine per sequential batch; the pytesseract executable setting is process-global.

- **Alignment:** ORB, unique ratio-filtered matches, RANSAC homography, inlier checks and geometric plausibility. ECC affine is the fallback. Only the printed image is transformed. Exact duplicates use identity. Metrics include both keypoint counts, matches, inliers, inlier ratio, transform and coverage. Invalid warp borders are excluded; inadequate coverage is flagged. Failed registration skips pixel analysis and cannot pass.
- **OCR:** Tesseract `image_to_data` provides text, confidence and coordinates in the aligned reference frame. Reference OCR runs once per batch. OCR and print preprocessing are separate; print contrast is preserved so faint ink is measurable. Tesseract's logical word order is retained, including RTL/mixed lines.
- **Text:** strict Unicode preserves Arabic letters, dots, diacritics, punctuation and numbers. Only whitespace is collapsed for comparison. Strict multiline text and whitespace-only diagnostic text are retained. Character, word and line differences are exported. CER/WER use Levenshtein distance divided by reference length; insertions can produce rates above 1. Text similarity is `1 - character_distance / max(reference_length, printed_length, 1)`. Word mismatches/deletions map to printed/reference boxes. Low-confidence differences remain review candidates rather than being silently discarded.
- **SSIM:** calculated on aligned grayscale with data range 255, averaged over valid interior pixels. Local SSIM differences also need meaningful intensity changes and minimum component area. SSIM is a structural metric, not a probability of correctness.
- **Ink:** threshold masks and a configurable registration tolerance detect extra/missing strokes. Intensity differences inside ink also expose fading and darkening. Ratios use valid compared page pixels as the denominator. Reports retain raw evidence counts and noise-filtered counts. Ratios and decisions use filtered counts.
- **Streaks/blobs:** directional morphology operates on extra/missing differences, preserving legitimate reference lines. Black/white compact components are labeled as blob candidates. These geometric categories are heuristics and can overlap other causes.
- **Merging:** IoU/containment merges supporting detections into local boxes, retaining algorithm labels and evidence. Specific text/streak boxes take priority over broad visual regions. Per-algorithm counts may exceed the final merged box count.

Every page has PASS or DEFECTIVE. PASS requires completed alignment, adequate coverage, available OCR, no localized defects and passing aggregate thresholds. **DEFECTIVE · review** means checks were incomplete; it does not assert a confirmed physical defect. Failures, unavailable OCR and insufficient coverage are never silently counted as clean. A low-confidence OCR page is also flagged for review. A page can fail an aggregate metric without a localized box; the details panel explains that reason.

Confidence on OCR defects is OCR recognition confidence. Classical detectors use rule-based evidence, not calibrated defect probabilities. OCR can misread an identical wrong character on both pages; a successful OCR call is not a guarantee of content accuracy. Empty OCR results on image-only/blank documents are valid. The classical visual checks remain active.

## Configuration and calibration

Use **Settings** for common controls. Advanced values are in `config.py`; put overrides in root `settings.json` (see `settings.example.json`). Invalid keys/values produce a startup error. Settings are captured in reports for reproducibility.

| Setting | Default | Meaning |
|---|---:|---|
| `ssim_threshold` | 0.95 | Minimum aggregate SSIM |
| `text_similarity_threshold` | 0.98 | Minimum text similarity |
| `extra_ink_threshold` | 0.002 | Maximum significant extra ink / compared pixels |
| `missing_ink_threshold` | 0.002 | Maximum significant missing ink / compared pixels |
| `min_defect_area` / `min_blob_area` | 40 | Minimum significant connected area in pixels |
| `min_line_length` | 100 | Minimum horizontal/vertical streak length |
| `registration_tolerance` | 1 | Ink mask neighbourhood tolerance in pixels |
| `ink_threshold` | 180 | Grayscale intensity below which pixels are ink |
| `intensity_difference` | 35 | Minimum meaningful grayscale difference |
| `ink_density_difference` | 80 | Minimum nearby stroke-core intensity change for fading/darkening |
| `min_coverage` | 0.95 | Required overlap after registration |
| `ocr_min_confidence` | 45 | OCR confidence below which review is required |

**These are experimental starting values, not validated manufacturing tolerances.** Localized significant defects also trigger failure even when whole-page averages pass. Calibrate on labeled clean and faulty pages at the intended scanner DPI. Increasing area/tolerance suppresses noise but may hide character dots or tiny broken strokes. Illumination, JPEG artifacts, paper texture and repeated layouts can affect registration and detection. No representative real-document dataset was supplied; synthetic verification cannot establish field accuracy.

## Saved outputs

Each run uses a timestamp and random suffix; repeated filenames cannot overwrite each other.

```text
outputs/batch_YYYYMMDD_HHMMSS_<id>/
  page_<filename>_<id>/
    original_printed.png
    aligned_printed.png
    valid_comparison_mask.png
    difference_mask.png
    extra_ink_mask.png
    missing_ink_mask.png
    detected_lines.png
    defect_overlay.png
    report.json
  batch_report.json
  batch_report.csv
```

Aligned outputs and box coordinates use the full **reference resolution**. Original Printed preserves the decoded input resolution. Thus scans at different DPI are resampled to the reference grid, not the GUI preview. Red annotation uses OpenCV BGR `(0, 0, 255)`. Labels stay short and in English; full Unicode expected/detected text is available in the GUI/JSON.

JSON includes settings, OCR words/confidence, character/word/line differences, registration quality, defect evidence and processing times. Per-image time includes image artifacts, excluding final JSON write; the first image in a direct API call can include reference preparation. The worker prepares the reference separately. Batch duration includes processing/reference preparation, excluding final batch report serialization. CSV uses UTF-8 with BOM for Excel and escapes formula-like string values. Outputs may contain document content; all processing and reporting run locally.

## Samples, tests and command-line inspection

```powershell
python tools\create_test_defects.py --demo
python tools\create_test_defects.py --input samples\standard.png --output samples\printed_custom.png --defects vertical blob missing rotation
python tools\inspect_batch.py --reference samples\standard.png --images samples\printed_clean.png samples\printed_defective.png samples\printed_misaligned.png
python -m compileall -q app.py config.py src gui tools tests
python -m unittest discover -s tests -v
python app.py --diagnose
python app.py --smoke-test
python tools\verify_gui.py
```

The generator supports vertical/horizontal streaks, blobs, missing print, blur, rotation and shift. The unittest suite covers Unicode I/O, failed/successful Tesseract validation, alignment, SSIM, ink/noise/streaks, Arabic/numeric text differences, merging, caching, red overlays and report generation. It mocks OCR for deterministic pipeline tests; real OCR is exercised separately without asserting exact text.

## Troubleshooting and optional features

- **Tesseract missing:** install the executable and set its exact path in Settings. `pip install pytesseract` alone does not install it.
- **Arabic missing:** install `ara.traineddata` in the configured data directory, then run `app.py --diagnose`. Never silently substitute English-only OCR.
- **Alignment failure:** choose matching full-page scans with enough features. Tiny/sparse or strongly cropped pages may need manual review. Resized fallback images are previews only; their pixel metrics are unavailable.
- **OCR timeout/failure:** check languages, scan quality and `ocr_timeout`. An incomplete result is saved with its error.
- **Cannot save outputs:** select a writable output directory. Inspect `logs/inspection.log`; failed writes are surfaced in the GUI/results.
- **GUI closes during processing:** closing requests cancellation and waits for the current image; the worker is never force-terminated.
- **Too many/too few boxes:** calibrate thresholds and acquisition conditions using known ground truth; retain the individual metrics for evaluation.
- **CNN/VGG16:** intentionally disabled and not installed. The complete core workflow uses OCR/classical CV. No Python downgrade or TensorFlow dependency is needed. There is no trained defect classifier or claimed model accuracy.

The code is a working prototype for evaluation, not a calibrated production acceptance system.

## Automatic text direction

Reference and printed pages are now normalized to an upright orientation **before** feature
preparation, alignment, OCR, and visual comparison. Supported corrections are 0, 90, 180,
and 270 degrees clockwise after EXIF handling. Arabic reading order is retained; strings
are never reversed to correct a rotated page.

The detector first tries optional `osd.traineddata`. Weak or unavailable OSD falls back to
four sparse-text OCR probes (`ara+eng` by default), requiring enough recognized characters,
confidence, and a clear score margin. Printed pages with ambiguous text may use a unique,
strong geometric match to a resolved reference. Scores are heuristic evidence, not calibrated
probabilities. Grid-heavy, blank, mixed-orientation, mirrored, and very sparse pages may need
manual review. Fine skew is left to existing registration, not this quarter-turn detector.

Use **Text direction...** beside Settings to choose Auto or a clockwise correction for the
reference and each printed page. Saving clears displayed results and requires another run.
Overrides apply to the current selection/session. Disabling automatic detection requires
explicit manual corrections (including 0 for already upright pages) for a completed inspection.
An unresolved direction cannot produce PASS. Cancellation still finishes the current page.

Settings in `settings.example.json` include `auto_orientation`, `orientation_timeout` (30 seconds
shared across probes), `orientation_max_dimension` (2000 pixels), OSD confidence (15), minimum
usable characters (12), recognition evidence confidence (55), and relative margin (0.20).
Native image analysis/registration can finish its current operation after the budget expires;
no further candidate is started, and incomplete candidate sets are not accepted. Tune thresholds
against representative documents. Original source files and inspection pixels are not filtered
or resized by quarter-turn correction; downsampling is restricted to direction analysis copies.

The reference panel and **Upright Reference**, **Upright Printed** tabs show normalized images.
**Original Printed** retains the loaded EXIF-corrected image. OCR boxes, masks, and defect overlays
use the normalized reference coordinate frame. Schema 2 JSON reports include direction evidence,
pixel-center rotation matrices and inverses, and the composed loaded-printed-to-reference transform.
CSV reports include correction angles and direction statuses. Reference caching also tracks
settings and manual corrections.

For the backend, `InspectionEngine(settings, reference_orientation=90,
printed_orientations={image_path: 180})` accepts optional overrides; omitted values use Auto.
`OCREngine.extract()` remains a low-level recognizer of supplied pixels. Use the inspection
engine, or `OrientationDetector(settings, ocr).detect(image)`, for automatic page normalization.

Run a reproducible live check (the expected correction is manually established ground truth):

```powershell
python tools\verify_orientation.py standard_image1.jpeg --expected-correction 90 --all-pairs
```

It records baseline/final OCR, an additional sparse-layout diagnostic, model hashes, all four
rotations, and all 16 reference/printed combinations under `outputs/orientation_validation`.
Orientation correctness does not guarantee complete or accurate OCR: the supplied Arabic form
still loses headings with the existing final `ocr_psm=3`, and sparse mode still makes character
errors. See `ORIENTATION_VALIDATION.md` for measured results and limits.
