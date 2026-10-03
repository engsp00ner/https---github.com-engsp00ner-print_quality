# Inspection pipeline diagnosis and fix

## Root cause

OCR did not suppress visual inspection in the original normal path. Once alignment
succeeded, `InspectionEngine.inspect()` ran SSIM, ink comparison, streak detection,
and blob detection before printed OCR and text comparison.

Two defects made visual results appear absent:

1. All visual detectors were in one unprotected block. An exception from SSIM,
   ink, streak, or blob processing reached the outer image-level exception handler,
   which discarded every partially collected visual result and marked the image as
   a generic processing error.
2. `merge_defects()` retained overlapping visual evidence under a `TEXT_ERROR`
   box, but the overlay and details displayed only `defect.type`. Because text has
   higher priority, the visible label was `TEXT_ERROR`, hiding `EXTRA_INK`,
   `MISSING_INK`, or `VISUAL_DIFFERENCE` in the evidence list.

The corrected execution path is:

`load -> orientation -> alignment -> independent SSIM / ink -> streak + blob -> OCR -> text comparison -> merge -> report -> UI`

Pixel checks are skipped, rather than reported as confirmed defects, when orientation,
alignment, or coverage is not trustworthy. OCR failure and low OCR confidence do not
skip pixel checks. They make text comparison incomplete or review-required.

## Changes

- `src/inspection_engine.py` records every check in `InspectionResult.checks` with
  status, duration, result count, and a failure/skip reason. Independent detector
  failures are caught locally, logged, reported, and do not erase other results.
- `src/models.py` serializes check status in image and batch reports.
- `src/defect_merging.py` keeps pixel evidence overlapping a text mismatch as
  `text_change_visual_evidence`; it is no longer presented as a second confirmed
  excess/missing-ink classification. Separate-region defects remain independent.
- `src/visualization.py` labels overlays with every confirmed merged classification.
- `gui/main_window.py` displays check states, type counts, all merged labels and
  evidence in the details panel. The defect-count cell tooltip contains type counts.
- `src/reporting.py` adds CSV check statuses, raw evidence counts, displayed counts,
  and text-reliability state.
- `tests/test_engine.py` adds execution/aggregation tests for text plus extra ink,
  text plus fading, low-confidence OCR, OCR failure, SSIM failure, and failed alignment.

## Capability status

| Check | Implemented | Runs with reliable orientation/alignment | Reported/UI |
| --- | --- | --- | --- |
| OCR/text comparison | Yes | OCR is independent of visual checks; needs reliable registration for boxes | Yes; low confidence is review-required |
| SSIM visual difference | Yes | Yes | Yes |
| Extra ink | Yes | Yes | Yes |
| Missing ink / fading | Yes; fading appears as missing-ink intensity evidence | Yes | Yes |
| Streaks | Yes; uses ink masks | After ink succeeds | Yes |
| Ink blobs | Yes; uses ink masks | After ink succeeds | Yes |
| Alignment | Yes | Required for confirmed pixel defects | Status/reason reported |
| Orientation | Yes | Required for confirmed pixel defects | Status/reason reported |

Classification limit: a changed character may produce both OCR differences and
pixel differences. Overlapping pixel evidence is retained for review but is not
automatically counted as a separate print-quality defect. Pixel evidence in a
separate region is still reported independently.

## Verification

- `python -m unittest discover -v`: full suite completed successfully before this
  focused rerun; the focused core/engine/UI suite passed 21 tests after the final
  classification adjustment.
- `python app.py --diagnose`: passed with `ara`, `eng`, and `osd` models.
- `python app.py --smoke-test`: completed successfully.
- Existing orientation tests cover differently rotated reference/printed pairs.

The injected OCR, SSIM, alignment, and output failures in tests produce expected
logged stack traces; assertions confirm that applicable independent results remain
available and the inspection is never marked PASS when a required check is incomplete.
