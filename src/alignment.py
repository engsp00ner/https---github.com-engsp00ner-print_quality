"""Register only the printed image, retaining validity and quality evidence."""
import cv2
import numpy as np


def prepare_features(gray, settings):
    scale = min(1.0, settings.alignment_max_dimension / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    keypoints, descriptors = cv2.ORB_create(nfeatures=settings.orb_features).detectAndCompute(small, None)
    return small, scale, keypoints, descriptors


def _plausible(matrix, printed_shape, reference_shape, settings):
    ph, pw = printed_shape[:2]
    rh, rw = reference_shape[:2]
    corners = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2)
    transformed = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    target = np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]])
    return (np.isfinite(transformed).all()
            and cv2.isContourConvex(transformed.reshape(-1, 1, 2))
            and cv2.contourArea(transformed, oriented=True) > 0
            and np.max(np.linalg.norm((transformed - target) / [rw, rh], axis=1))
            <= settings.max_corner_displacement)


def align(reference_gray, printed, settings, reference_features=None):
    from .preprocessing import grayscale
    pg = grayscale(printed)
    rh, rw = reference_gray.shape
    features = reference_features or prepare_features(reference_gray, settings)
    rs, rscale, rkp, rd = features
    ps, pscale, pkp, pd = prepare_features(pg, settings)
    info = {"status": "FAILED", "method": "none", "reference_keypoints": len(rkp),
            "printed_keypoints": len(pkp), "matches": 0, "inliers": 0, "inlier_ratio": 0.0,
            "coverage": 0.0, "warnings": []}
    matrix = None
    if pg.shape == reference_gray.shape and np.array_equal(pg, reference_gray):
        matrix = np.eye(3)
        info.update(status="SUCCESS", method="IDENTICAL", correlation=1.0)
    elif rd is not None and pd is not None:
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(pd, rd, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < settings.match_ratio * pair[1].distance]
        # Do not count multiple printed features matched to one reference feature.
        unique = {}
        for match in sorted(good, key=lambda m: m.distance):
            unique.setdefault(match.trainIdx, match)
        good = list(unique.values())
        info["matches"] = len(good)
        if len(good) >= settings.min_matches:
            source = np.float32([pkp[m.queryIdx].pt for m in good]) / pscale
            target = np.float32([rkp[m.trainIdx].pt for m in good]) / rscale
            candidate, inliers = cv2.findHomography(source, target, cv2.RANSAC, settings.ransac_threshold / rscale)
            info["inliers"] = int(inliers.sum()) if inliers is not None else 0
            info["inlier_ratio"] = info["inliers"] / len(good)
            if (candidate is not None and info["inliers"] >= settings.min_matches
                    and info["inlier_ratio"] >= settings.min_inlier_ratio
                    and _plausible(candidate, pg.shape, reference_gray.shape, settings)):
                matrix = candidate
                info.update(status="SUCCESS", method="ORB_RANSAC")
    if matrix is None:
        info["warnings"].append("ORB alignment unavailable or rejected; attempted ECC affine fallback.")
        try:
            resized = cv2.resize(pg, (rs.shape[1], rs.shape[0]))
            warp = np.eye(2, 3, dtype=np.float32)
            correlation, warp = cv2.findTransformECC(
                rs.astype(np.float32) / 255, resized.astype(np.float32) / 255, warp,
                cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                                   settings.ecc_iterations, 1e-5), None, 5)
            reference_to_small = np.diag([rs.shape[1] / rw, rs.shape[0] / rh, 1.])
            printed_to_small = np.diag([rs.shape[1] / pg.shape[1], rs.shape[0] / pg.shape[0], 1.])
            candidate = np.linalg.inv(reference_to_small) @ np.linalg.inv(np.vstack([warp, [0, 0, 1]])) @ printed_to_small
            if correlation >= settings.min_alignment_correlation and _plausible(candidate, pg.shape, reference_gray.shape, settings):
                matrix = candidate
                info.update(status="FALLBACK", method="ECC_AFFINE", correlation=float(correlation))
        except (cv2.error, np.linalg.LinAlgError) as exc:
            info["warnings"].append(f"ECC could not register this image: {str(exc).splitlines()[0]}")
    if matrix is None:
        info["warnings"].append("Alignment failed. Pixel comparisons were skipped; manual review required.")
        return cv2.resize(printed, (rw, rh)), np.zeros((rh, rw), np.uint8), info
    aligned = cv2.warpPerspective(printed, matrix, (rw, rh), borderValue=(255, 255, 255))
    valid = cv2.warpPerspective(np.full(pg.shape, 255, np.uint8), matrix, (rw, rh), flags=cv2.INTER_NEAREST)
    valid = cv2.erode(valid, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    info.update(coverage=float(np.count_nonzero(valid) / valid.size), homography=matrix.tolist())
    if info["coverage"] < settings.min_coverage:
        info["warnings"].append("Insufficient page coverage after alignment; manual review required.")
    return aligned, valid, info
