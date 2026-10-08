"""Cheap per-frame checks that run on every frame, before any model sees it.

Blur: variance of the Laplacian. Sharp edges (spine lettering) give high variance;
motion blur smears them and the variance drops.
Glare: fraction of pixels clipped to pure white (a lamp or window reflected off a glossy spine).
A white sheet of paper is bright but not clipped, so it does not count.
"""

import cv2
import numpy as np

from app import config


def measure(jpeg: bytes) -> tuple[float, float]:
    """Return (sharpness, glare_fraction) for a JPEG frame."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0, 0.0
    sharpness = float(cv2.Laplacian(img, cv2.CV_64F).var())
    glare = float((img >= 254).mean())
    return sharpness, glare


def problem(sharpness: float, glare: float) -> str | None:
    """A short description of what is wrong with the frame, or None if it is usable."""
    if sharpness < config.BLUR_THRESHOLD:
        return "blurry"
    if glare > config.GLARE_THRESHOLD:
        return "glare"
    return None
