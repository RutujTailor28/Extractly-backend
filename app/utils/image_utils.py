"""Image preprocessing utilities for document pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.core.config import settings


@dataclass
class ProcessingMetadata:
    original_width: int | None = None
    original_height: int | None = None
    rotation_applied: int = 0
    deskew_angle: float = 0.0
    contrast_normalized: bool = False
    output_format: str = "png"
    output_width: int | None = None
    output_height: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = {
            "original_width": self.original_width,
            "original_height": self.original_height,
            "rotation_applied": self.rotation_applied,
            "deskew_angle": self.deskew_angle,
            "contrast_normalized": self.contrast_normalized,
            "output_format": self.output_format,
            "output_width": self.output_width,
            "output_height": self.output_height,
        }
        payload.update(self.extra)
        return payload


def load_image_from_bytes(content: bytes) -> Image.Image:
    """Load a Pillow image from raw bytes."""
    image = Image.open(BytesIO(content))
    image.load()
    return image


def correct_orientation(image: Image.Image) -> tuple[Image.Image, int]:
    """
    Apply EXIF orientation only.

    Does not guess orientation via OCR/AI.
    Returns (corrected_image, rotation_degrees_applied_estimate).
    """
    before = image.copy()
    corrected = ImageOps.exif_transpose(image) or image

    # Best-effort rotation estimate from size swap (90/270) — EXIF details vary
    rotation = 0
    if before.size != corrected.size:
        rotation = 90

    # Drop EXIF after correcting so downstream tools don't re-apply
    if hasattr(corrected, "getexif"):
        cleaned = corrected.copy()
        cleaned.info.pop("exif", None)
        return cleaned.convert("RGB"), rotation

    return corrected.convert("RGB"), rotation


def _pil_to_cv(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _cv_to_pil(image_bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def detect_and_correct_skew(
    image: Image.Image,
    *,
    max_angle: float | None = None,
) -> tuple[Image.Image, float]:
    """
    Detect and correct small document skew using OpenCV.

    Limits correction to ±max_angle. Returns unchanged image when confidence is low.
    """
    limit = settings.MAX_DESKEW_ANGLE if max_angle is None else max_angle
    cv_image = _pil_to_cv(image)
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return image, 0.0

    angle = cv2.minAreaRect(coords)[-1]
    # OpenCV returns [-90, 0); normalize to a small deskew angle
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.2 or abs(angle) > limit:
        return image, 0.0

    (h, w) = cv_image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        cv_image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return _cv_to_pil(rotated), float(round(angle, 3))


def normalize_contrast(image: Image.Image) -> Image.Image:
    """Conservative contrast enhancement suitable for vision models."""
    cv_image = _pil_to_cv(image)
    lab = cv2.cvtColor(cv_image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    merged = cv2.merge((l_channel, a_channel, b_channel))
    enhanced = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    return _cv_to_pil(enhanced)


def normalize_resolution(
    image: Image.Image,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
) -> Image.Image:
    """Downscale oversized images while preserving aspect ratio."""
    width_limit = max_width or settings.MAX_IMAGE_WIDTH
    height_limit = max_height or settings.MAX_IMAGE_HEIGHT
    width, height = image.size

    if width <= width_limit and height <= height_limit:
        return image

    scale = min(width_limit / width, height_limit / height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def preprocess_image(content: bytes) -> tuple[bytes, ProcessingMetadata, int, int]:
    """
    Full preprocessing pipeline.

    Returns: (png_bytes, metadata, width, height)
    """
    image = load_image_from_bytes(content)
    metadata = ProcessingMetadata(
        original_width=image.width,
        original_height=image.height,
    )

    image, rotation = correct_orientation(image)
    metadata.rotation_applied = rotation

    image, deskew_angle = detect_and_correct_skew(image)
    metadata.deskew_angle = deskew_angle

    image = normalize_contrast(image)
    metadata.contrast_normalized = True

    image = normalize_resolution(image)
    metadata.output_width = image.width
    metadata.output_height = image.height
    metadata.output_format = "png"

    return image_to_png_bytes(image), metadata, image.width, image.height
