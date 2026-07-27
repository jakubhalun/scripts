"""Shared helpers for building test fixtures without touching the network."""

from __future__ import annotations

from typing import Any

import commons_duplicate_finder as finder


def make_file(title: str, metadata: dict[str, str] | None = None, **overrides: Any) -> finder.CommonsFile:
    """Build a CommonsFile with normalized metadata already applied."""
    item = finder.CommonsFile(title=title, raw_metadata=dict(metadata or {}))
    for name, value in overrides.items():
        setattr(item, name, value)
    item.normalized = finder.normalize_metadata(item.raw_metadata)
    return item


def photo_metadata(
    timestamp: str = "2020:06:01 10:00:00",
    make: str = "Canon",
    model: str = "EOS 5D",
    lens: str | None = "EF 24-70mm",
    exposure: str = "1/125",
    aperture: str = "5.6",
    iso: str = "200",
    focal: str = "50",
    **extra: str,
) -> dict[str, str]:
    """Build a plausible EXIF dictionary; pass None to leave a field out."""
    metadata = {
        "DateTimeOriginal": timestamp,
        "Make": make,
        "Model": model,
        "LensModel": lens,
        "ExposureTime": exposure,
        "FNumber": aperture,
        "ISOSpeedRatings": iso,
        "FocalLength": focal,
    }
    metadata.update(extra)
    return {name: value for name, value in metadata.items() if value is not None}
