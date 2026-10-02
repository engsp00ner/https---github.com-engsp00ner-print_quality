from .regions import regions


def detect_blobs(extra, missing, settings):
    result = []
    for polarity, mask in (("black", extra), ("white", missing)):
        for defect in regions(mask, "INK_BLOB", settings.min_blob_area):
            _, _, w, h = defect.bbox
            fill = defect.details["area"] / (w * h)
            if max(w, h) / max(1, min(w, h)) < settings.blob_max_aspect and fill >= settings.blob_min_fill:
                defect.details.update(polarity=polarity, fill_ratio=fill)
                result.append(defect)
    return result
