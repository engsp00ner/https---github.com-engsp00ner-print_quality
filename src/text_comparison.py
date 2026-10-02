"""Strict Unicode comparison; diagnostic normalization changes whitespace only."""
from difflib import SequenceMatcher
from .models import Defect


def edit_distance(a, b):
    if len(a) < len(b):
        a, b = b, a
    # Common prefix/suffix trimming makes mostly identical pages inexpensive.
    start = 0
    while start < len(b) and a[start] == b[start]:
        start += 1
    a, b = a[start:], b[start:]
    end = 0
    while end < len(b) and a[-end - 1] == b[-end - 1]:
        end += 1
    if end:
        a, b = a[:-end], b[:-end]
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _differences(a, b):
    return [{"operation": op, "reference_range": [i, j], "printed_range": [k, l],
             "expected": a[i:j], "detected": b[k:l]}
            for op, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal"]


def compare_text(reference, printed, settings):
    a, b = " ".join(reference.strict_text.split()), " ".join(printed.strict_text.split())
    aw, bw = [w.text for w in reference.words], [w.text for w in printed.words]
    char_distance, word_distance = edit_distance(a, b), edit_distance(aw, bw)
    changes = _differences(aw, bw)
    defects = []
    for change in changes:
        i, j = change["reference_range"]
        k, l = change["printed_range"]
        ref_words, new_words = reference.words[i:j], printed.words[k:l]
        # Pair replacements per word to keep boxes local. Deletions use reference coordinates.
        for index in range(max(len(ref_words), len(new_words))):
            expected = ref_words[index] if index < len(ref_words) else None
            detected = new_words[index] if index < len(new_words) else None
            anchor = detected or expected
            confidence = min(w.confidence for w in (expected, detected) if w is not None)
            details = {"expected": expected.text if expected else "", "detected": detected.text if detected else "",
                       "operation": change["operation"], "ocr_confidence": confidence,
                       "needs_review": confidence < settings.ocr_min_confidence,
                       "character_differences": _differences(expected.text if expected else "", detected.text if detected else "")}
            defects.append(Defect("TEXT_ERROR", *anchor.bbox, max(0, confidence / 100), details))
    return {"character_error_rate": char_distance / max(1, len(a)),
            "word_error_rate": word_distance / max(1, len(aw)),
            "text_similarity": max(0.0, 1 - char_distance / max(1, len(a), len(b))),
            "character_edit_distance": char_distance, "word_edit_distance": word_distance,
            "character_differences": _differences(a, b), "word_differences": changes,
            "line_differences": _differences(reference.strict_text.splitlines(), printed.strict_text.splitlines())}, defects
