# Plan: detect text orientation before OCR and image comparison

Status: planning only. No application changes are implemented by this document.

Implementation follow-up (2026-10-03): the application now implements the orientation
workflow below. See `ORIENTATION_VALIDATION.md` for verified behavior, measured live
results, and remaining OCR/layout limitations. This document retains the original plan.

## Objective and scope

Automatically recognize pages presented at 0, 90, 180, or 270 degrees, normalize the reference and each printed image to an upright reading orientation, then run alignment, OCR, and defect comparison in a consistent coordinate system. Support Arabic, English, and mixed Arabic/English pages while retaining logical right-to-left text order.

Page rotation and Arabic right-to-left reading order are different problems. Rotation must change pixels; it must not reverse strings or sort Arabic words by increasing x coordinate. Mixed-orientation text regions on a single page and mirrored scans are outside the first implementation; flag unsupported cases for review rather than promising automatic recovery. Fine skew correction is separate from quarter-turn orientation and should be evaluated after this baseline works.

## Findings and limits of the diagnosis

- `standard_image.jpeg` is 1600 x 1131 pixels with no EXIF orientation tag. Applying a **90-degree clockwise correction** makes its text upright. This describes the correction, not the input's rotation angle.
- `src/image_loader.py` applies EXIF orientation, but cannot infer orientation from the image content.
- `src/preprocessing.py::for_ocr()` only converts to grayscale. `src/ocr_engine.py` passes that image to OCR with the configured page segmentation mode, currently 3, and has no explicit orientation stage.
- `src/inspection_engine.py` extracts reference OCR before alignment and aligns printed pages to that reference. Correcting only OCR inputs would leave visual comparison and OCR boxes in different coordinate frames.
- `src/alignment.py::_plausible()` limits corner displacement. It should continue checking ordinary registration; do not loosen this guard to compensate for unhandled quarter-turn rotations.
- The previous local run could not perform recognition because the configured Tesseract executable was missing. The configured `D:\masters\code\tessdata` directory was also absent. Orientation is a strong diagnosis from the pixels and code, but its measured OCR improvement still requires a working runtime and before/after extraction.

## 1. Establish a reproducible baseline

1. Validate the actual Tesseract executable and the configured Arabic and English language files using `validate_tesseract()` and `app.py --diagnose`. Locate valid paths before proposing settings changes; do not assume that the local `tessdata` directory contains usable models.
2. Check whether `osd.traineddata` is available for orientation/script detection. Treat it as an optional capability with an explicit fallback, not as a replacement for `ara` and `eng` recognition models.
3. Run the existing `load_image()` and `OCREngine.extract()` path on the original image. Record exact text, words, boxes, confidence, runtime, model/runtime versions, and settings.
4. Repeat with an in-memory clockwise correction and otherwise identical OCR settings. Independently transcribe the readable headers, checking uncertain labels against the image. Do not use the earlier approximate transcription as unquestioned ground truth.
5. Store diagnostic outputs separately from source images. Measure recognition errors and missing headers against that transcription; confidence alone is not accuracy.

## 2. Add a reusable orientation detector

Create `src/orientation.py` with a detector and an exact quarter-turn transform helper. Input is the image after EXIF handling; output includes:

- Status: confident, ambiguous, no usable text evidence, unavailable, or manually overridden.
- Correction angle, consistently defined as clockwise degrees in `{0, 90, 180, 270}`.
- Method, evidence, candidate scores, best-versus-runner-up margin, elapsed time, and warnings.
- Original/normalized dimensions and the forward/inverse coordinate transforms.

Proposed detection sequence:

1. Use Tesseract orientation/script detection when available and sufficiently confident. Verify the returned angle convention against known rotated fixtures before applying it.
2. When detection fails, evidence is sparse, or confidence is inadequate, evaluate the four rotations using lightweight OCR on analysis copies. Use the configured recognition languages and the same scoring procedure for every candidate.
3. Score candidates using multiple signals: usable word/character count, confidence weighted by recognized text length, plausible text-line geometry, and penalties for punctuation/grid artifacts. Require both minimum evidence and a sufficient margin over the next candidate. Calibrate thresholds on real forms; do not select from mean confidence alone.
4. For table-heavy pages, test text-rich regions and sparse-text segmentation on diagnostic copies. If grid suppression is evaluated, apply it only to detection/OCR analysis copies and verify it preserves Arabic dots. Do not base orientation on table-line direction alone.
5. If the evidence remains inconclusive, return an explicit unresolved state and offer a manual angle. Do not silently mark a guessed angle as confident. Blank pages may have no meaningful text orientation.

Keep orientation probes separate from final `OCREngine.extract()` so probes do not recursively trigger orientation. Share the existing Tesseract lock, executable selection, environment restoration, and timeout handling with all new OCR/OSD calls. Give detection a total per-page time budget, not four unbounded calls. Use bounded analysis resolution while retaining sufficient text detail; retry higher resolution only within that budget.

## 3. Normalize pages before preparing comparison data

Reference flow:

`load + EXIF -> determine correction -> quarter-turn pixels -> grayscale/mask/features -> final reference OCR -> cache`

Printed-image flow:

`load + EXIF -> determine correction -> quarter-turn pixels -> align to normalized reference -> final OCR + visual comparison`

Use exact transpose/flip or `cv2.rotate` operations for quarter turns, avoiding resampling and preserving original files. Cache the reference orientation, normalized image, features, and OCR together. Include effective orientation/override and OCR settings in cache invalidation, alongside the existing file signature.

If printed-page text orientation is ambiguous but the reference is trustworthy, evaluate candidate rotations using existing registration checks as supplementary evidence. Accept only a clearly superior geometrically valid candidate with adequate coverage and correlation. Repeated table grids can align in more than one direction, so ties require review. Do not choose a rotation merely because it minimizes OCR text differences or defect counts; those differences may be real defects.

If reference orientation is unresolved, require a manual reference correction before declaring an inspection complete. Printed pages with unresolved orientation must also remain review-required. Ordinary registration checks still apply after correction.

## 4. Preserve coordinate meaning and artifacts

Use the upright reference as the canonical coordinate frame for OCR boxes, masks, localized defects, and overlays. Final printed OCR runs on the aligned image, so its boxes naturally share this frame.

Record transforms with explicit source/destination frames. If `R_print` maps the EXIF-corrected printed image to its normalized orientation and `H` maps that orientation to the upright reference, the complete mapping is `H @ R_print`. Store the reference rotation separately. Use the inverse composition when an original-image overlay is requested. Validate corner conventions and width/height swaps to prevent off-by-one errors.

Retain the original preview and add normalized reference/printed artifacts where useful. Define “original” as the existing loaded, EXIF-corrected image; raw-file coordinates would additionally require the EXIF transform. Do not draw canonical boxes directly onto an unrotated preview.

## 5. Settings, UI, reports, and review behavior

- Add auto-orientation enablement, detection timeout/budget, and calibrated evidence/margin thresholds to `config.py`. Document optional OSD availability and fallback behavior.
- Provide Auto and explicit clockwise correction choices in the UI for the reference and individual printed pages. Manual changes invalidate the relevant cache and results and require rerunning inspection.
- Show correction angle, detection method, and unresolved warnings near the image/OCR result. Keep detection confidence distinct from recognition confidence.
- Add orientation metadata and frame definitions to JSON reports, with a schema-version update if necessary; add compact angle/status fields to CSV if useful.
- Include required orientation resolution in `inspection_complete` and PASS decisions. A successful OCR process does not prove that orientation or recognized text is correct. Preserve the existing review behavior for missing OCR and low confidence.

Expected files: new `src/orientation.py`; updates to `config.py`, `src/models.py`, `src/ocr_engine.py`, `src/inspection_engine.py`, `src/reporting.py`, `gui/main_window.py`, settings examples, and README. Touch `gui/worker.py` only as needed for progress/cancellation and per-image overrides. Keep `src/alignment.py` safeguards unless targeted evidence shows a separate defect. Preserve existing user edits in the GUI and PDF conversion files.

## 6. Verification and acceptance criteria

1. Unit-test all four quarter-turn transforms on asymmetric images, forward/inverse point and box mapping, odd dimensions, and no double correction after EXIF handling.
2. Test OSD success, insufficient evidence, missing OSD data, timeouts, fallback ties, blank pages, and manual overrides. Verify executable/environment state restoration for every probe path.
3. Run live Arabic/English recognition on `standard_image.jpeg` and its four rotated variants. The supplied original should select a 90-degree clockwise correction; every variant should normalize to the same upright pixel arrangement. Compare OCR against manually verified headers and report remaining recognition errors.
4. Compare all reference/printed rotation combinations for the same page. Verify canonical dimensions, alignment, consistent OCR, and properly positioned boxes. Use exact quarter-turn PNG fixtures to isolate orientation from JPEG recompression artifacts.
5. Repeat with known missing ink, added ink, text changes, and streaks. Those defects must remain detectable regardless of starting orientation. Existing clean-page expectations should still pass when OCR and orientation are sufficiently reliable.
6. Include sparse Arabic forms, English pages, mixed scripts, repeated grids, already upright images, small skew, and blank pages. Ambiguous examples must produce review states rather than confident guesses.
7. Run the relevant existing core, engine, GUI, and live OCR tests. Extend the GUI checks for overrides and normalized previews. Live OCR checks must report missing-runtime skips explicitly; a skipped test is not evidence of OCR accuracy.
8. Measure per-page latency and reference-cache reuse. Select default thresholds and detection budgets from these results, recording tested models and known limitations.

## Delivery sequence

1. Capture baseline and verify runtime/model configuration.
2. Implement detector, transform contract, and focused tests.
3. Integrate normalization and caching before alignment/OCR.
4. Add reports, review decisions, UI overrides, and documentation.
5. Validate the supplied image and representative comparison pages; publish measured before/after text and remaining errors.

Completion means orientation handling works across the tested rotation combinations, coordinates remain correct, real defects remain visible, and uncertain cases are clearly review-required. It does not mean every Arabic character will be recognized perfectly; any remaining OCR/layout issues should be measured separately after orientation is corrected.
