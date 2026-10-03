"""Strict Unicode comparison; diagnostic normalization changes whitespace only."""
from difflib import SequenceMatcher
from dataclasses import replace
from .models import Defect, OCRWord


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
    if reference.metadata.get('granularity') == printed.metadata.get('granularity') == 'line':
        return compare_regions(reference, printed, settings)
    return compare_sequence(reference, printed, settings)


def compare_sequence(reference, printed, settings):
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


def compare_regions(reference, printed, settings):
    """Bipartite spatial components allow one line to match several fragments.

    Each component retains genuine region geometry. No invented word positions.
    Returned transcription remains untouched; reading order below is only for
    comparing spatially corresponding groups in canonical reference pixels.
    """
    regions = reference.words + printed.words
    n = len(reference.words)
    parent = list(range(len(regions)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i, a in enumerate(reference.words):
        ax, ay, aw, ah = a.bbox
        for j, b in enumerate(printed.words, n):
            bx, by, bw, bh = b.bbox
            vertical = min(ay + ah, by + bh) - max(ay, by)
            horizontal = min(ax + aw, bx + bw) - max(ax, bx)
            if vertical >= .35 * min(ah, bh) and horizontal >= -min(8, .25 * min(ah, bh)):
                parent[root(j)] = root(i)
    groups = {}
    for i, region in enumerate(regions):
        groups.setdefault(root(i), [[], []])[i >= n].append(region)

    def combine(parts):
        if not parts:
            return None
        rtl = any('\u0600' <= c <= '\u06ff' for p in parts for c in p.text)
        # Sort fragments on the same baseline in the script's reading direction.
        parts = sorted(parts, key=lambda p: (-p.bbox[0] if rtl else p.bbox[0]))
        x, y = min(p.bbox[0] for p in parts), min(p.bbox[1] for p in parts)
        right = max(p.bbox[0] + p.bbox[2] for p in parts)
        bottom = max(p.bbox[1] + p.bbox[3] for p in parts)
        return OCRWord(' '.join(p.text for p in parts), min(p.confidence for p in parts),
                       (x, y, right - x, bottom - y), parts[0].line_id, granularity='region_group')

    defects, differences, expected, detected = [], [], [], []
    for left, right in groups.values():
        a, b = combine(left), combine(right)
        ra = replace(reference, strict_text=a.text if a else '', words=[a] if a else [])
        rb = replace(printed, strict_text=b.text if b else '', words=[b] if b else [])
        metrics, local = compare_sequence(ra, rb, replace(settings, ocr_min_confidence=settings.paddle_min_confidence))
        for defect in local:
            defect.details.update(granularity='region_group', reference_regions=[p.polygon for p in left],
                                  printed_regions=[p.polygon for p in right])
        defects.extend(local)
        differences.extend(metrics['word_differences'])
        expected.append(ra.strict_text)
        detected.append(rb.strict_text)
    # Segmentation-independent text metrics; localization comes from groups above.
    a, b = ' '.join(' '.join(expected).split()), ' '.join(' '.join(detected).split())
    distance = edit_distance(a, b)
    word_distance = edit_distance(a.split(), b.split())
    return dict(character_error_rate=distance / max(1, len(a)),
                word_error_rate=word_distance / max(1, len(a.split())),
                text_similarity=max(0., 1 - distance / max(1, len(a), len(b))),
                character_edit_distance=distance, word_edit_distance=word_distance,
                character_differences=_differences(a, b), word_differences=differences,
                line_differences=differences, matching='spatial_region_components'), defects
