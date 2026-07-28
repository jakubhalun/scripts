#!/usr/bin/env python3
# Commands:
# python3 -m venv .venv
# source .venv/bin/activate
# python -m pip install -r wikimedia/commons_duplicates/requirements.txt
# python wikimedia/commons_duplicates/commons_duplicate_finder.py --requesting-user NAME --target-user NAME
"""Find likely duplicate photographs among files uploaded to Wikimedia Commons.

What this script does
---------------------
It retrieves the list of files currently uploaded by one Wikimedia Commons user,
downloads their technical metadata and EXIF tags through the public MediaWiki API,
normalizes the relevant fields, groups files whose metadata is identical or nearly
identical, and writes a standalone HTML report listing the suspicious groups.

The report is a list of *candidates for manual review*. It is not proof that any two
files are duplicates. Cameras in burst mode routinely produce several genuinely
different photographs that share the same second-rounded timestamp, ISO, aperture,
shutter speed, focal length, camera body and lens.

Read-only guarantee
-------------------
The script only performs anonymous HTTP GET requests against
``https://commons.wikimedia.org/w/api.php``. It never logs in, never requests an edit
token, and never sends a write action. It does not edit Commons, does not nominate
files for deletion, does not add templates and does not leave messages anywhere.

Metadata only
-------------
This version uses file metadata and EXIF exclusively. It does not download original
images or thumbnails, does not perform any visual comparison, and does not calculate
perceptual hashes. Consequently it cannot detect a duplicate whose EXIF was stripped,
and it cannot tell a re-crop apart from an unrelated frame of the same burst.

Requirements
------------
* Python 3.11 or newer
* ``requests`` (see ``requirements.txt``)

Virtual environment setup and installation
------------------------------------------
Linux/macOS::

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt

Windows PowerShell::

    py -m venv .venv
    .venv\\Scripts\\Activate.ps1
    python -m pip install -r requirements.txt

Example usage
-------------
Run::

    python commons_duplicate_finder.py \\
        --requesting-user RequestingUserName \\
        --target-user ExampleUser

With a slower request rate, a custom report location and a JSON dump::

    python commons_duplicate_finder.py \\
        --requesting-user RequestingUserName \\
        --target-user ExampleUser \\
        --request-delay 1.5 \\
        --output report.html \\
        --json-output report.json

Generated files
---------------
``commons-duplicates-<target-user>.html``
    The standalone HTML report (default name, override with ``--output``). It embeds
    its own CSS, references no external JavaScript, CSS or CDN, and contains no
    images -- only links to Commons file description pages.

``report.json`` (only with ``--json-output``)
    Normalized metadata, raw metadata, groups, similarity scores, classifications,
    run statistics and the list of errors and skipped files.

``.cache/commons-duplicate-finder/*.json`` (unless ``--no-cache``)
    Cached successful API responses, keyed by a hash of the request parameters. The
    cache is local only, holds no credentials, and is safe to delete at any time.

Run the tests
-------------
From this directory::

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import quote

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_NAME = "CommonsExifDuplicateFinder"
TOOL_VERSION = "0.1"

API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
COMMONS_PAGE_BASE = "https://commons.wikimedia.org/wiki/"

REQUEST_TIMEOUT = (10, 60)
DEFAULT_REQUEST_DELAY = 1.0
MINIMUM_REQUEST_DELAY = 1.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_BACKOFF = 2.0
DEFAULT_MAX_BACKOFF = 60.0
DEFAULT_MAX_RETRY_AFTER = 300.0
BACKOFF_JITTER = 1.0

ALLIMAGES_BATCH_LIMIT = 500
METADATA_BATCH_SIZE = 50

DEFAULT_NEAR_TIMESTAMP_SECONDS = 1
LARGE_GROUP_THRESHOLD = 50
WEAK_MATCH_SCORE_THRESHOLD = 60

DEFAULT_CACHE_DIR = Path(".cache") / "commons-duplicate-finder"

# Editing software sometimes writes a stale DateTimeOriginal while leaving the real
# time in DateTimeDigitized. A whole-hour difference is only a timezone disagreement
# about the same instant; anything else means the two tags contradict each other.
MAX_TIMEZONE_OFFSET_SECONDS = 26 * 3600

CAPTURE_CORROBORATED = "corroborated by DateTimeDigitized"
CAPTURE_NO_SECONDARY = "no second timestamp to check against"
CAPTURE_TIMEZONE_OFFSET = "DateTimeDigitized differs by a whole-hour timezone offset"
CAPTURE_CONTRADICTED = "contradicted by DateTimeDigitized"

# A genuine per-image identifier should be shared by a handful of files at most. A value
# shared by more than this is a camera or software constant, not an image identity.
MAX_SHARED_IDENTIFIER_FILES = 10

CLASSIFICATION_EXACT = "Exact binary duplicate"
CLASSIFICATION_SAME_SOURCE = "Same source image"
CLASSIFICATION_VERY_STRONG = "Very strong metadata match"
CLASSIFICATION_STRONG = "Strong metadata match"
CLASSIFICATION_BURST = "Possible duplicate or burst sequence"
CLASSIFICATION_RELATED = "Possible related frames"
CLASSIFICATION_WEAK = "Weak match"

logger = logging.getLogger("commons_duplicate_finder")


class CommonsApiError(RuntimeError):
    """Raised when a Commons API request cannot be completed."""


# ---------------------------------------------------------------------------
# Metadata field definitions
# ---------------------------------------------------------------------------

# Canonical field name -> EXIF/MediaWiki source tags, in order of preference.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "make": ("Make", "CameraMake", "Manufacturer"),
    "model": ("Model", "CameraModel", "UniqueCameraModel"),
    "lens": ("LensModel", "Lens", "LensInfo", "LensID"),
    "exposure": ("ExposureTime", "ShutterSpeedValue"),
    "aperture": ("FNumber", "ApertureValue"),
    "iso": ("ISOSpeedRatings", "PhotographicSensitivity", "ISOSpeed", "ISO"),
    "focal_length": ("FocalLength",),
    "width": ("ImageWidth", "ExifImageWidth", "PixelXDimension"),
    "height": ("ImageLength", "ExifImageHeight", "PixelYDimension"),
    "orientation": ("Orientation",),
    # XMP identifier of the image an export was derived from. Two exports of one
    # photograph share it; separate frames do not. ImageUniqueID is deliberately absent:
    # some phones write a single constant value into every file they produce, which
    # would collapse hundreds of unrelated photographs into one group.
    "source_image_id": ("OriginalDocumentID", "DerivedFromOriginalDocumentID", "DocumentID"),
    # Metered scene brightness. It is a measurement rather than a setting, so two frames
    # of the same scene almost always differ while two exports of one frame agree.
    "brightness": ("BrightnessValue",),
    "serial": (
        "SerialNumber",
        "BodySerialNumber",
        "CameraSerialNumber",
        "InternalSerialNumber",
    ),
}

# Capture timestamp candidates, strongest first. The Commons upload timestamp is
# deliberately absent: it says when the file reached Commons, not when the photograph
# was taken.
CAPTURE_TIMESTAMP_TAGS: tuple[str, ...] = (
    "DateTimeOriginal",
    "DateTimeDigitized",
    "SubSecDateTimeOriginal",
    "CreateDate",
    "DateTimeCreated",
    "DateCreated",
    "DateTime",
)

# Similarity scoring. Weights are deliberately plain numbers so that every score in
# the report can be recomputed by hand from the listed matching fields.
FIELD_WEIGHTS: dict[str, int] = {
    "source_image_id": 45,
    "capture_timestamp": 40,
    "camera": 15,
    "serial": 10,
    "lens": 10,
    "exposure": 8,
    "aperture": 8,
    "iso": 8,
    "focal_length": 8,
    "brightness": 5,
    "orientation": 3,
}

FIELD_LABELS: dict[str, str] = {
    "source_image_id": "Source image identifier",
    "capture_timestamp": "Capture timestamp",
    "camera": "Camera make and model",
    "serial": "Camera serial number",
    "lens": "Lens model",
    "exposure": "Exposure time",
    "aperture": "Aperture",
    "iso": "ISO",
    "focal_length": "Focal length",
    "brightness": "Metered brightness",
    "orientation": "Orientation",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetadataField:
    """One normalized metadata field together with the value it came from."""

    source: str
    raw: str
    key: str | None


@dataclass
class NormalizedMetadata:
    """Comparable representation of the EXIF tags used for grouping."""

    capture_timestamp: datetime | None = None
    capture_source: str | None = None
    capture_raw: str | None = None
    digitized_timestamp: datetime | None = None
    digitized_raw: str | None = None
    capture_status: str = CAPTURE_NO_SECONDARY
    fields: dict[str, MetadataField] = field(default_factory=dict)

    def comparison_key(self, name: str) -> str | None:
        """Return the normalized value used for grouping, or None when unusable.

        When a file's own DateTimeDigitized contradicts its DateTimeOriginal, the
        capture identity includes both tags. Files whose DateTimeOriginal collides only
        because editing software rewrote it therefore no longer group together.
        """
        if name == "capture_timestamp":
            if self.capture_timestamp is None:
                return None
            if self.capture_status == CAPTURE_CONTRADICTED and self.digitized_timestamp is not None:
                return f"{self.capture_timestamp.isoformat()}|{self.digitized_timestamp.isoformat()}"
            return self.capture_timestamp.isoformat()
        if name == "camera":
            make = self.comparison_key("make")
            model = self.comparison_key("model")
            if make is None and model is None:
                return None
            return f"{make or ''}|{model or ''}"
        entry = self.fields.get(name)
        if entry is None:
            return None
        return entry.key

    def raw_value(self, name: str) -> str:
        """Return the untouched value as reported by the API, for display."""
        if name == "capture_timestamp":
            return self.capture_raw or ""
        if name == "camera":
            parts = [self.raw_value("make"), self.raw_value("model")]
            return " ".join(part for part in parts if part)
        entry = self.fields.get(name)
        return entry.raw if entry is not None else ""

    @property
    def has_usable_exif(self) -> bool:
        return self.capture_timestamp is not None or bool(self.fields)

    @property
    def has_contradicted_capture(self) -> bool:
        return self.capture_status == CAPTURE_CONTRADICTED


@dataclass
class CommonsFile:
    """A single file currently uploaded by the analyzed user."""

    title: str
    pageid: int | None = None
    upload_timestamp: str = ""
    sha1: str = ""
    width: int | None = None
    height: int | None = None
    size: int | None = None
    mime: str = ""
    mediatype: str = ""
    raw_metadata: dict[str, str] = field(default_factory=dict)
    normalized: NormalizedMetadata = field(default_factory=NormalizedMetadata)

    @property
    def page_url(self) -> str:
        return COMMONS_PAGE_BASE + quote(self.title.replace(" ", "_"), safe=":/")

    @property
    def dimensions(self) -> str:
        if self.width and self.height:
            return f"{self.width} x {self.height}"
        return ""


@dataclass
class GroupEvidence:
    """Why a group was selected, expressed as plain field lists and a total."""

    score: int
    matching: list[str] = field(default_factory=list)
    near_matching: list[str] = field(default_factory=list)
    differing: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass
class MatchGroup:
    """A set of files reported together, with its classification and evidence."""

    level: str
    classification: str
    files: list[CommonsFile]
    evidence: GroupEvidence
    notes: list[str] = field(default_factory=list)


@dataclass
class SkippedFile:
    """A file that could not be analyzed, kept so the report stays honest."""

    title: str
    reason: str


@dataclass
class RunStats:
    """Counters shown in the report header."""

    total_files: int = 0
    files_with_metadata: int = 0
    files_without_exif: int = 0
    files_with_conflicting_timestamps: int = 0
    api_requests: int = 0
    cache_hits: int = 0


# ---------------------------------------------------------------------------
# Throttling, retries and HTTP access
# ---------------------------------------------------------------------------


def build_user_agent(requesting_user: str) -> str:
    """Build an identifiable User-Agent naming the person running the script."""
    encoded_requesting_user = quote(requesting_user.replace(" ", "_"), safe="")
    return (
        f"{TOOL_NAME}/{TOOL_VERSION} "
        f"(https://commons.wikimedia.org/wiki/User:{encoded_requesting_user}; "
        "contact: user page)"
    )


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Convert a Retry-After header into seconds, or None when it is unusable."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0.0, (target - reference).total_seconds())


def backoff_delay(attempt: int, initial: float, maximum: float) -> float:
    """Bounded exponential backoff with a small random jitter."""
    base = min(initial * (2**attempt), maximum)
    return base + random.uniform(0.0, BACKOFF_JITTER)


@dataclass
class RetryPolicy:
    """Configuration for the retry loop."""

    max_retries: int = DEFAULT_MAX_RETRIES
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF
    max_backoff: float = DEFAULT_MAX_BACKOFF
    max_retry_after: float = DEFAULT_MAX_RETRY_AFTER


class ResponseCache:
    """Local on-disk cache of successful API responses.

    One JSON file per request, named after a SHA-256 hash of the sorted request
    parameters. Failed responses are never stored, so a cache hit always replays a
    response that the API actually returned with HTTP 200.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def cache_key(params: dict[str, str]) -> str:
        payload = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, params: dict[str, str]) -> Path:
        return self.directory / f"{self.cache_key(params)}.json"

    def read(self, params: dict[str, str]) -> dict[str, Any] | None:
        path = self._path(params)
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.debug("Ignoring unreadable cache entry %s: %s", path.name, exc)
            return None
        response = document.get("response")
        return response if isinstance(response, dict) else None

    def write(self, params: dict[str, str], response: dict[str, Any]) -> None:
        document = {
            "tool": f"{TOOL_NAME}/{TOOL_VERSION}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "response": response,
        }
        path = self._path(params)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Could not write cache entry %s: %s", path.name, exc)


class CommonsApiClient:
    """Sequential, throttled, retrying read-only client for the Commons API.

    All requests go through a single :class:`requests.Session`. There is no threading,
    no asynchronous I/O and no connection pooling across workers: requests are issued
    one after another with a configurable minimum delay between them.
    """

    def __init__(
        self,
        user_agent: str,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        retry_policy: RetryPolicy | None = None,
        cache: ResponseCache | None = None,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.request_delay = request_delay
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self.cache = cache
        self._sleep = sleep
        self.request_count = 0
        self.cache_hits = 0
        self._last_request_at: float | None = None

    def get(self, params: dict[str, str]) -> dict[str, Any]:
        """Fetch one API response, using the cache when it already holds the answer."""
        if self.cache is not None:
            cached = self.cache.read(params)
            if cached is not None:
                self.cache_hits += 1
                logger.debug("Cache hit for %s", _describe_request(params))
                return cached
        payload = self._request_with_retries(params)
        if self.cache is not None:
            self.cache.write(params, payload)
        return payload

    def iterate_query(self, params: dict[str, str]) -> Iterator[dict[str, Any]]:
        """Yield every page of a query, following MediaWiki continuation."""
        current = dict(params)
        while True:
            payload = self.get(current)
            yield payload
            continuation = payload.get("continue")
            if not isinstance(continuation, dict) or not continuation:
                return
            current = dict(params)
            current.update({key: str(value) for key, value in continuation.items()})

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self.request_delay - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            logger.debug("Waiting %.1f seconds before next request...", remaining)
            self._sleep(remaining)

    def _wait_before_retry(self, attempt: int, reason: str, retry_after: str | None) -> None:
        policy = self.retry_policy
        if attempt >= policy.max_retries:
            raise CommonsApiError(f"Giving up after {policy.max_retries} retries: {reason}")
        delay = parse_retry_after(retry_after)
        if delay is not None:
            if delay > policy.max_retry_after:
                raise CommonsApiError(
                    f"Server asked to wait {delay:.0f} seconds, which exceeds the "
                    f"--max-retry-after limit of {policy.max_retry_after:.0f} seconds"
                )
            delay += random.uniform(0.0, BACKOFF_JITTER)
        else:
            delay = backoff_delay(attempt, policy.initial_backoff, policy.max_backoff)
        logger.warning(
            "%s Retrying after %.1f seconds (attempt %d of %d)...",
            reason,
            delay,
            attempt + 1,
            policy.max_retries,
        )
        self._sleep(delay)

    def _request_with_retries(self, params: dict[str, str]) -> dict[str, Any]:
        attempt = 0
        while True:
            self._throttle()
            self._last_request_at = time.monotonic()
            self.request_count += 1
            try:
                response = self.session.get(
                    API_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as exc:
                self._wait_before_retry(attempt, f"Request failed ({exc.__class__.__name__}).", None)
                attempt += 1
                continue

            status = response.status_code
            if status == 429:
                header = response.headers.get("Retry-After")
                self._wait_before_retry(attempt, "Received HTTP 429.", header)
                attempt += 1
                continue
            if 500 <= status < 600:
                self._wait_before_retry(attempt, f"Received HTTP {status}.", None)
                attempt += 1
                continue
            if status >= 400:
                raise CommonsApiError(f"HTTP {status} for {_describe_request(params)}")

            try:
                payload = response.json()
            except ValueError:
                self._wait_before_retry(attempt, "Received an incomplete response.", None)
                attempt += 1
                continue
            if not isinstance(payload, dict):
                self._wait_before_retry(attempt, "Received an unexpected response shape.", None)
                attempt += 1
                continue

            error = payload.get("error")
            if isinstance(error, dict):
                code = error.get("code", "unknown")
                info = error.get("info", "no details")
                raise CommonsApiError(f"API error '{code}': {info}")
            return payload


def _describe_request(params: dict[str, str]) -> str:
    """Short request description for logs; never includes the full response."""
    return params.get("list") or params.get("prop") or params.get("action", "request")


# ---------------------------------------------------------------------------
# Commons API access
# ---------------------------------------------------------------------------


def _base_query_params() -> dict[str, str]:
    return {"action": "query", "format": "json", "formatversion": "2"}


def fetch_user_files(
    client: CommonsApiClient, target_user: str, limit: int | None = None
) -> list[CommonsFile]:
    """Retrieve every file whose current version was uploaded by ``target_user``."""
    logger.info("Fetching upload list for %s...", target_user)
    params = _base_query_params() | {
        "list": "allimages",
        "aiuser": target_user,
        "aisort": "timestamp",
        "aidir": "newer",
        "ailimit": str(ALLIMAGES_BATCH_LIMIT),
        "aiprop": "timestamp|user|size|dimensions|sha1|mime|mediatype",
    }

    files: list[CommonsFile] = []
    seen_titles: set[str] = set()
    for payload in client.iterate_query(params):
        items = payload.get("query", {}).get("allimages", [])
        for item in items:
            title = item.get("title")
            if not isinstance(title, str) or title in seen_titles:
                continue
            seen_titles.add(title)
            files.append(
                CommonsFile(
                    title=title,
                    upload_timestamp=str(item.get("timestamp") or ""),
                    sha1=str(item.get("sha1") or ""),
                    width=_as_int(item.get("width")),
                    height=_as_int(item.get("height")),
                    size=_as_int(item.get("size")),
                    mime=str(item.get("mime") or ""),
                    mediatype=str(item.get("mediatype") or ""),
                )
            )
        logger.info("Retrieved %d files...", len(files))
        if limit is not None and len(files) >= limit:
            break

    if limit is not None:
        files = files[:limit]
    return files


def fetch_metadata(
    client: CommonsApiClient, files: Sequence[CommonsFile], skipped: list[SkippedFile]
) -> None:
    """Fill in raw metadata for every file, in batches, tolerating batch failures."""
    batches = [
        list(files[start : start + METADATA_BATCH_SIZE])
        for start in range(0, len(files), METADATA_BATCH_SIZE)
    ]
    for index, batch in enumerate(batches, start=1):
        logger.info("Fetching metadata batch %d of %d...", index, len(batches))
        params = _base_query_params() | {
            "prop": "imageinfo",
            "titles": "|".join(item.title for item in batch),
            "iiprop": "timestamp|user|size|dimensions|sha1|mime|mediatype|metadata|commonmetadata",
        }
        by_title = {item.title: item for item in batch}
        try:
            for payload in client.iterate_query(params):
                _apply_metadata_payload(payload, by_title, skipped)
        except CommonsApiError as exc:
            logger.error("Metadata batch %d failed: %s", index, exc)
            for item in batch:
                skipped.append(SkippedFile(item.title, f"metadata batch failed: {exc}"))


def _apply_metadata_payload(
    payload: dict[str, Any],
    by_title: dict[str, CommonsFile],
    skipped: list[SkippedFile],
) -> None:
    """Copy one imageinfo response onto the matching files."""
    query = payload.get("query", {})
    aliases = dict(by_title)
    for entry in query.get("normalized", []):
        source = entry.get("from")
        target = entry.get("to")
        if isinstance(source, str) and isinstance(target, str) and source in by_title:
            aliases[target] = by_title[source]

    for page in query.get("pages", []):
        title = page.get("title")
        current = aliases.get(title) if isinstance(title, str) else None
        if current is None:
            continue
        if page.get("missing"):
            skipped.append(SkippedFile(current.title, "page missing or deleted during the run"))
            continue
        imageinfo = page.get("imageinfo")
        if not isinstance(imageinfo, list) or not imageinfo:
            skipped.append(SkippedFile(current.title, "no image information returned"))
            continue

        info = imageinfo[0]
        current.pageid = _as_int(page.get("pageid"))
        current.sha1 = str(info.get("sha1") or current.sha1)
        current.width = _as_int(info.get("width")) or current.width
        current.height = _as_int(info.get("height")) or current.height
        current.size = _as_int(info.get("size")) or current.size
        current.mime = str(info.get("mime") or current.mime)
        current.mediatype = str(info.get("mediatype") or current.mediatype)

        merged: dict[str, str] = {}
        merged.update(flatten_metadata(info.get("metadata")))
        merged.update(flatten_metadata(info.get("commonmetadata")))
        current.raw_metadata = merged


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Metadata extraction and normalization
# ---------------------------------------------------------------------------


def flatten_metadata(entries: Any) -> dict[str, str]:
    """Flatten a MediaWiki ``name``/``value`` metadata list into a plain dictionary."""
    result: dict[str, str] = {}
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        value = entry.get("value")
        if _is_metadata_list(value):
            result.update(flatten_metadata(value))
            continue
        text = stringify_metadata_value(value)
        if text:
            result[name] = text
    return result


def _is_metadata_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and "name" in item for item in value)
    )


def stringify_metadata_value(value: Any) -> str:
    """Render an arbitrary metadata value as a display string."""
    if value is None or isinstance(value, bool):
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [stringify_metadata_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("x-default", "en", "value"):
            if key in value:
                return stringify_metadata_value(value[key])
        parts = [
            stringify_metadata_value(item)
            for key, item in sorted(value.items())
            if not key.startswith("_")
        ]
        return "; ".join(part for part in parts if part)
    return str(value)


def lookup_raw(metadata: dict[str, str], aliases: Sequence[str]) -> tuple[str, str] | None:
    """Find the first present alias, matching tag names case-insensitively."""
    lowered = {name.lower(): (name, value) for name, value in metadata.items()}
    for alias in aliases:
        found = lowered.get(alias.lower())
        if found is not None and found[1].strip():
            return alias, found[1].strip()
    return None


def normalize_text(value: str) -> str | None:
    """Trim, collapse whitespace and underscores, and casefold for comparison."""
    collapsed = re.sub(r"[\s_]+", " ", value).strip()
    if not collapsed:
        return None
    return collapsed.casefold()


def normalize_rational(value: Any) -> Fraction | None:
    """Parse ``10/500``, ``1/125 sec``, ``f/5.6``, ``50 mm`` or ``0.008`` numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(value).limit_denominator(1_000_000)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    ratio = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", text)
    if ratio is not None:
        numerator = Fraction(ratio.group(1))
        denominator = Fraction(ratio.group(2))
        if denominator == 0:
            return None
        return numerator / denominator
    number = re.search(r"-?\d+(?:\.\d+)?", text)
    if number is None:
        return None
    return Fraction(number.group(0)).limit_denominator(1_000_000)


def normalize_exposure(source: str, value: str) -> str | None:
    """Return a canonical shutter speed such as ``1/125`` or ``2.5s``.

    ``ShutterSpeedValue`` is an APEX value, so it is converted to seconds first. Small
    decimal roundings (``0.0167`` versus ``1/60``) collapse onto the same reciprocal.
    """
    parsed = normalize_rational(value)
    if parsed is None:
        return None
    seconds = float(parsed)
    if source == "ShutterSpeedValue":
        seconds = 2.0**-seconds
    if seconds <= 0:
        return None
    if seconds >= 1:
        return f"{round(seconds, 2)}s"
    reciprocal = 1.0 / seconds
    rounded = round(reciprocal)
    if rounded > 0 and abs(reciprocal - rounded) / reciprocal <= 0.005:
        return f"1/{rounded}"
    return f"1/{round(reciprocal, 1)}"


def normalize_aperture(source: str, value: str) -> str | None:
    """Return a canonical f-number; APEX ``ApertureValue`` is converted first."""
    parsed = normalize_rational(value)
    if parsed is None:
        return None
    number = float(parsed)
    if source == "ApertureValue":
        number = 2.0 ** (number / 2.0)
    if number <= 0:
        return None
    return f"{round(number, 2):g}"


def normalize_iso(value: Any) -> str | None:
    """Return the ISO value as an integer string when it can be read as a number."""
    if isinstance(value, str) and ";" in value:
        value = value.split(";", 1)[0]
    parsed = normalize_rational(value)
    if parsed is None or parsed <= 0:
        return None
    return str(int(round(float(parsed))))


def normalize_focal_length(value: str) -> str | None:
    """Return the focal length in millimetres, rounded to two decimals."""
    parsed = normalize_rational(value)
    if parsed is None or parsed <= 0:
        return None
    return f"{round(float(parsed), 2):g}"


def normalize_orientation(value: str) -> str | None:
    """Return the EXIF orientation as an integer string when it is one."""
    parsed = normalize_rational(value)
    if parsed is None:
        return normalize_text(value)
    return str(int(round(float(parsed))))


def normalize_dimension(value: str) -> str | None:
    """Return a pixel dimension as an integer string."""
    parsed = normalize_rational(value)
    if parsed is None or parsed <= 0:
        return None
    return str(int(round(float(parsed))))


def parse_exif_datetime(value: str) -> datetime | None:
    """Parse the usual EXIF datetime spellings, rejecting placeholder values."""
    text = value.strip()
    if not text:
        return None
    text = re.sub(r"\s*(?:Z|[+-]\d{2}:?\d{2})$", "", text).strip()
    text = text.split(".", 1)[0].strip()
    if text.startswith("0000"):
        return None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def select_capture_timestamp(metadata: dict[str, str]) -> tuple[datetime, str, str] | None:
    """Pick the strongest available capture timestamp and say where it came from.

    The Commons upload timestamp is never a candidate; it is reported separately.
    """
    lowered = {name.lower(): value for name, value in metadata.items()}
    for tag in CAPTURE_TIMESTAMP_TAGS:
        raw = lowered.get(tag.lower())
        if not isinstance(raw, str) or not raw.strip():
            continue
        parsed = parse_exif_datetime(raw)
        if parsed is not None:
            return parsed, tag, raw.strip()
    return None


def classify_capture_timestamp(
    capture: datetime | None, digitized: datetime | None
) -> str:
    """Judge whether DateTimeDigitized backs up or contradicts the capture timestamp.

    Editing software regularly rewrites one of the two tags. A difference of an exact
    number of whole hours is only a timezone disagreement about the same instant and is
    treated as corroboration; a difference of, say, a day or a month means the two tags
    describe different moments and the capture timestamp cannot be trusted on its own.
    """
    if capture is None or digitized is None:
        return CAPTURE_NO_SECONDARY
    if digitized == capture:
        return CAPTURE_CORROBORATED
    difference = abs((digitized - capture).total_seconds())
    if difference % 3600 == 0 and difference <= MAX_TIMEZONE_OFFSET_SECONDS:
        return CAPTURE_TIMEZONE_OFFSET
    return CAPTURE_CONTRADICTED


_NORMALIZERS = {
    "make": lambda source, value: normalize_text(value),
    "model": lambda source, value: normalize_text(value),
    "lens": lambda source, value: normalize_text(value),
    "serial": lambda source, value: normalize_text(value),
    "exposure": normalize_exposure,
    "aperture": normalize_aperture,
    "iso": lambda source, value: normalize_iso(value),
    "focal_length": lambda source, value: normalize_focal_length(value),
    "orientation": lambda source, value: normalize_orientation(value),
    "width": lambda source, value: normalize_dimension(value),
    "height": lambda source, value: normalize_dimension(value),
}


def normalize_metadata(metadata: dict[str, str]) -> NormalizedMetadata:
    """Build the comparable view of one file's metadata, keeping the raw values."""
    result = NormalizedMetadata()
    capture = select_capture_timestamp(metadata)
    if capture is not None:
        result.capture_timestamp, result.capture_source, result.capture_raw = capture

    digitized = lookup_raw(metadata, ("DateTimeDigitized",))
    if digitized is not None:
        parsed = parse_exif_datetime(digitized[1])
        if parsed is not None:
            result.digitized_timestamp = parsed
            result.digitized_raw = digitized[1]
    if result.capture_source != "DateTimeDigitized":
        result.capture_status = classify_capture_timestamp(
            result.capture_timestamp, result.digitized_timestamp
        )

    for name, aliases in FIELD_ALIASES.items():
        found = lookup_raw(metadata, aliases)
        if found is None:
            continue
        source, raw = found
        normalizer = _NORMALIZERS.get(name)
        try:
            key = normalizer(source, raw) if normalizer is not None else normalize_text(raw)
        except (ArithmeticError, ValueError, TypeError) as exc:
            logger.debug("Could not normalize %s=%r: %s", source, raw, exc)
            key = None
        result.fields[name] = MetadataField(source=source, raw=raw, key=key)
    return result


# ---------------------------------------------------------------------------
# Candidate grouping
# ---------------------------------------------------------------------------


class _DisjointSet:
    """Tracks which files have already been reported together at a stronger level."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        parent = self._parent.setdefault(item, item)
        while parent != self._parent[parent]:
            self._parent[parent] = self._parent[self._parent[parent]]
            parent = self._parent[parent]
        return parent

    def union(self, items: Sequence[str]) -> None:
        if not items:
            return
        root = self.find(items[0])
        for item in items[1:]:
            other = self.find(item)
            if other != root:
                self._parent[other] = root

    def adds_information(self, items: Sequence[str]) -> bool:
        """True when the items are not already all in the same reported component."""
        return len({self.find(item) for item in items}) > 1


LEVEL_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "level1",
        CLASSIFICATION_VERY_STRONG,
        ("capture_timestamp", "camera", "lens", "exposure", "aperture", "iso", "focal_length"),
    ),
    (
        "level2",
        CLASSIFICATION_STRONG,
        ("capture_timestamp", "camera", "exposure", "aperture", "iso", "focal_length"),
    ),
    ("level3", CLASSIFICATION_BURST, ("capture_timestamp", "camera")),
)

# Levels that describe a sequence of frames rather than one repeated photograph. They
# are only reported when the user explicitly asks for them.
SERIES_LEVELS = frozenset({"level3", "level4"})


def _group_key(item: CommonsFile, fields: Sequence[str]) -> tuple[str, ...] | None:
    keys = [item.normalized.comparison_key(name) for name in fields]
    if any(key is None for key in keys):
        return None
    return tuple(key for key in keys if key is not None)


def _index_by_key(
    files: Sequence[CommonsFile], fields: Sequence[str]
) -> list[list[CommonsFile]]:
    buckets: dict[tuple[str, ...], list[CommonsFile]] = defaultdict(list)
    for item in files:
        key = _group_key(item, fields)
        if key is not None:
            buckets[key].append(item)
    return [bucket for bucket in buckets.values() if len(bucket) > 1]


def _near_timestamp_chains(
    files: Sequence[CommonsFile], tolerance_seconds: int
) -> list[list[CommonsFile]]:
    """Chain files whose exposure metadata matches and whose timestamps nearly match."""
    bucket_fields = ("camera", "exposure", "aperture", "iso", "focal_length")
    buckets: dict[tuple[str, ...], list[CommonsFile]] = defaultdict(list)
    for item in files:
        # Chaining compares raw timestamps, so a capture time that its own file
        # contradicts must not take part in it.
        if item.normalized.capture_timestamp is None or item.normalized.has_contradicted_capture:
            continue
        key = _group_key(item, bucket_fields)
        if key is not None:
            buckets[key].append(item)

    chains: list[list[CommonsFile]] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        ordered = sorted(bucket, key=lambda entry: (entry.normalized.capture_timestamp, entry.title))
        chain = [ordered[0]]
        for previous, current in zip(ordered, ordered[1:]):
            gap = abs(
                (current.normalized.capture_timestamp - previous.normalized.capture_timestamp).total_seconds()
            )
            if gap <= tolerance_seconds:
                chain.append(current)
            else:
                if len(chain) > 1:
                    chains.append(chain)
                chain = [current]
        if len(chain) > 1:
            chains.append(chain)
    return chains


def group_by_source_image_id(files: Sequence[CommonsFile]) -> list[list[CommonsFile]]:
    """Group exports that XMP says came from the same source photograph.

    Values shared by an implausible number of files are ignored: some phones write one
    constant identifier into everything they produce, which is a camera identity rather
    than an image identity.
    """
    buckets: dict[str, list[CommonsFile]] = defaultdict(list)
    for item in files:
        identifier = item.normalized.comparison_key("source_image_id")
        if identifier:
            buckets[identifier].append(item)
    groups = []
    for identifier, bucket in buckets.items():
        if len(bucket) < 2:
            continue
        if len(bucket) > MAX_SHARED_IDENTIFIER_FILES:
            logger.debug(
                "Ignoring source image identifier shared by %d files; it is a camera "
                "or software constant rather than an image identity.",
                len(bucket),
            )
            continue
        groups.append(bucket)
    return groups


def has_brightness_conflict(files: Sequence[CommonsFile]) -> bool:
    """True when two files report different metered brightness, so they differ as frames.

    Only files that actually carry the tag take part; a missing value proves nothing.
    Editing software often strips it, which is why this can only ever split a group and
    never join one.
    """
    measured = {
        key
        for key in (item.normalized.comparison_key("brightness") for item in files)
        if key is not None
    }
    return len(measured) > 1


def group_by_sha1(files: Sequence[CommonsFile]) -> list[list[CommonsFile]]:
    """Group files sharing the SHA-1 hash Commons computed for the file itself."""
    buckets: dict[str, list[CommonsFile]] = defaultdict(list)
    for item in files:
        if item.sha1:
            buckets[item.sha1].append(item)
    return [bucket for bucket in buckets.values() if len(bucket) > 1]


def build_groups(
    files: Sequence[CommonsFile],
    near_timestamp_seconds: int = DEFAULT_NEAR_TIMESTAMP_SECONDS,
    include_series: bool = False,
) -> list[MatchGroup]:
    """Build every reported group, strongest evidence first.

    Levels are evaluated from strongest to weakest and a weaker group is only emitted
    when it connects files that no stronger group already reported together, so the
    same set never appears in four sections at once.

    Groups that describe a burst or a sequence rather than one repeated photograph are
    withheld unless ``include_series`` is set. They still consume their files, so a
    weaker level cannot report the same set again through the back door.
    """
    logger.info("Building candidate groups...")
    reported = _DisjointSet()
    groups: list[MatchGroup] = []

    def emit(level: str, classification: str, members: Sequence[CommonsFile]) -> None:
        titles = [item.title for item in members]
        if not reported.adds_information(titles):
            return
        reported.union(titles)
        ordered = sorted(members, key=lambda item: item.title)
        evidence = evaluate_group(ordered, near_timestamp_seconds)
        label = classification

        brightness_conflict = level != "sha1" and has_brightness_conflict(ordered)
        is_series = level in SERIES_LEVELS or brightness_conflict
        if is_series and not include_series:
            return
        if level in {"level3", "level4"} and evidence.score < WEAK_MATCH_SCORE_THRESHOLD:
            label = CLASSIFICATION_WEAK
        notes: list[str] = []
        if brightness_conflict:
            notes.append(
                "These files report different metered brightness, so they are different "
                "frames of the same scene rather than copies of one photograph."
            )
        conflicting = sum(1 for item in ordered if item.normalized.has_contradicted_capture)
        if conflicting:
            notes.append(
                f"{conflicting} of these files carry a DateTimeOriginal that their own "
                "DateTimeDigitized contradicts, usually because editing software rewrote "
                "it. Their capture timestamp is unreliable, so this group rests on weaker "
                "evidence than the score suggests."
            )
        if len(ordered) >= LARGE_GROUP_THRESHOLD:
            notes.append(
                f"Unusually large group ({len(ordered)} files). This often means a "
                "placeholder or default capture timestamp rather than duplicated content."
            )
        groups.append(MatchGroup(level, label, list(ordered), evidence, notes))

    for bucket in group_by_sha1(files):
        emit("sha1", CLASSIFICATION_EXACT, bucket)

    for bucket in group_by_source_image_id(files):
        emit("source", CLASSIFICATION_SAME_SOURCE, bucket)

    with_timestamp = [item for item in files if item.normalized.capture_timestamp is not None]
    for level, classification, fields in LEVEL_DEFINITIONS:
        for bucket in _index_by_key(with_timestamp, fields):
            emit(level, classification, bucket)

    for chain in _near_timestamp_chains(with_timestamp, near_timestamp_seconds):
        emit("level4", CLASSIFICATION_RELATED, chain)

    return groups


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------


def _timestamps_within(files: Sequence[CommonsFile], tolerance_seconds: int) -> bool:
    stamps = [item.normalized.capture_timestamp for item in files]
    if any(stamp is None for stamp in stamps):
        return False
    ordered = sorted(stamp for stamp in stamps if stamp is not None)
    return (ordered[-1] - ordered[0]).total_seconds() <= tolerance_seconds


def evaluate_group(
    files: Sequence[CommonsFile], near_timestamp_seconds: int = DEFAULT_NEAR_TIMESTAMP_SECONDS
) -> GroupEvidence:
    """Score a group and list which fields match, nearly match, differ or are missing.

    A field counts as matching only when every file in the group reports the same
    normalized value. If any file lacks the field it is reported as missing rather
    than as a difference, because absent EXIF is not evidence either way.
    """
    evidence = GroupEvidence(score=0)
    for name, weight in FIELD_WEIGHTS.items():
        label = FIELD_LABELS[name]
        keys = [item.normalized.comparison_key(name) for item in files]
        if any(key is None for key in keys):
            evidence.missing.append(label)
            continue
        if len(set(keys)) == 1:
            evidence.matching.append(label)
            evidence.score += weight
            continue
        if name == "capture_timestamp" and _timestamps_within(files, near_timestamp_seconds):
            evidence.near_matching.append(label)
            evidence.score += weight // 2
            continue
        evidence.differing.append(label)
    return evidence


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

REPORT_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       margin: 0 auto; max-width: 1200px; padding: 1.5rem; line-height: 1.5; }
h1 { margin-bottom: 0.25rem; }
h2 { margin-top: 2.5rem; border-bottom: 2px solid currentColor; padding-bottom: 0.25rem; }
h3 { margin-bottom: 0.25rem; }
.subtitle { opacity: 0.75; margin-top: 0; }
.caution { border: 2px solid #b36b00; background: rgba(255, 176, 0, 0.12);
           padding: 0.75rem 1rem; border-radius: 6px; margin: 1.5rem 0; }
.caution p:first-child { margin-top: 0; }
.caution p:last-child { margin-bottom: 0; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
           gap: 0.5rem 1.5rem; margin: 1rem 0 0; padding: 0; }
.summary div { border-left: 3px solid rgba(127, 127, 127, 0.5); padding-left: 0.75rem; }
.summary dt { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.7; }
.summary dd { margin: 0; font-size: 1.15rem; font-weight: 600; }
.group { border: 1px solid rgba(127, 127, 127, 0.45); border-radius: 6px;
         padding: 0.75rem 1rem 1rem; margin: 1.25rem 0; }
.badge { display: inline-block; font-size: 0.8rem; font-weight: 700; padding: 0.15rem 0.5rem;
         border-radius: 999px; border: 1px solid currentColor; margin-left: 0.5rem; }
.evidence { margin: 0.5rem 0 0.75rem; padding: 0; list-style: none; font-size: 0.9rem; }
.evidence li { margin: 0.15rem 0; }
.evidence .label { font-weight: 600; }
.note { font-size: 0.9rem; font-style: italic; opacity: 0.85; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { border: 1px solid rgba(127, 127, 127, 0.4); padding: 0.3rem 0.5rem;
         text-align: left; vertical-align: top; }
th { background: rgba(127, 127, 127, 0.15); }
td.sha1 { font-family: ui-monospace, "SFMono-Regular", Consolas, monospace; font-size: 0.75rem;
          word-break: break-all; }
.empty { opacity: 0.7; font-style: italic; }
.conflict { color: #b36b00; font-size: 0.75rem; }
footer { margin-top: 3rem; font-size: 0.85rem; opacity: 0.75; }
.wrap { overflow-x: auto; }
"""

SECTION_TITLES: dict[str, str] = {
    "sha1": "Exact binary duplicates",
    "source": "Same source image",
    "level1": "Very strong metadata match",
    "level2": "Strong metadata match",
    "level3": "Possible duplicates or burst sequences",
    "level4": "Possible related frames",
}

SECTION_INTROS: dict[str, str] = {
    "sha1": (
        "These files have the same SHA-1 hash on Commons, which means their bytes are "
        "identical. This is the only section where duplication is certain."
    ),
    "source": (
        "These files carry the same XMP source image identifier, meaning they were "
        "exported from one and the same photograph. After exact binary duplicates this "
        "is the strongest evidence the tool can offer, and it survives cropping, "
        "resizing and re-encoding. It is only available on files whose editing software "
        "wrote the identifier. Note that two different crops of one original share the "
        "identifier too, so a group here can legitimately contain different pictures cut "
        "from the same frame."
    ),
    "level1": (
        "Every important EXIF field that both files report is identical, including the "
        "lens. Dimensions were deliberately ignored, so crops and resizes still appear "
        "here. A burst sequence can still produce this pattern."
    ),
    "level2": (
        "All available capture metadata matches, but lens information is missing from at "
        "least one file. Treat this as a candidate, not a finding."
    ),
    "level3": (
        "Capture timestamp, camera make and camera model match. This is exactly the "
        "pattern produced by a burst sequence, so these groups very often contain "
        "different photographs. Review each one visually."
    ),
    "level4": (
        "Capture timestamps differ by a small amount while the exposure metadata matches "
        "exactly. This is the weakest evidence in the report and usually indicates "
        "consecutive frames rather than duplicates."
    ),
}

TABLE_COLUMNS: tuple[str, ...] = (
    "File",
    "Commons link",
    "Upload timestamp",
    "Capture timestamp",
    "Camera",
    "Lens",
    "Exposure",
    "Aperture",
    "ISO",
    "Focal length",
    "Dimensions",
    "File size",
    "SHA-1",
)


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def format_file_size(size: int | None) -> str:
    """Render a byte count in a compact human-readable form."""
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB")
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def _render_file_row(item: CommonsFile) -> str:
    normalized = item.normalized
    capture = ""
    if normalized.capture_timestamp is not None:
        capture = normalized.capture_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        if normalized.capture_source:
            capture += f" ({normalized.capture_source})"
    capture_cell = _escape(capture)
    if normalized.has_contradicted_capture and normalized.digitized_timestamp is not None:
        digitized = normalized.digitized_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        capture_cell += (
            '<br><span class="conflict">contradicted by DateTimeDigitized '
            f"{_escape(digitized)}</span>"
        )
    cells = [
        f'<td>{_escape(item.title)}</td>',
        f'<td><a href="{_escape(item.page_url)}" rel="noopener noreferrer">file page</a></td>',
        f"<td>{_escape(item.upload_timestamp)}</td>",
        f"<td>{capture_cell}</td>",
        f"<td>{_escape(normalized.raw_value('camera'))}</td>",
        f"<td>{_escape(normalized.raw_value('lens'))}</td>",
        f"<td>{_escape(normalized.raw_value('exposure'))}</td>",
        f"<td>{_escape(normalized.raw_value('aperture'))}</td>",
        f"<td>{_escape(normalized.raw_value('iso'))}</td>",
        f"<td>{_escape(normalized.raw_value('focal_length'))}</td>",
        f"<td>{_escape(item.dimensions)}</td>",
        f"<td>{_escape(format_file_size(item.size))}</td>",
        f'<td class="sha1">{_escape(item.sha1)}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _render_evidence(evidence: GroupEvidence) -> str:
    rows = [
        ("Matching fields", evidence.matching),
        ("Nearly matching fields", evidence.near_matching),
        ("Differing fields", evidence.differing),
        ("Missing fields", evidence.missing),
    ]
    parts = ['<ul class="evidence">']
    for label, values in rows:
        if not values:
            continue
        joined = ", ".join(values)
        parts.append(f'<li><span class="label">{_escape(label)}:</span> {_escape(joined)}</li>')
    parts.append("</ul>")
    return "".join(parts)


def _render_group(index: int, group: MatchGroup) -> str:
    parts = ['<section class="group">']
    parts.append(
        f"<h3>Group {index}: {len(group.files)} files"
        f'<span class="badge">{_escape(group.classification)}</span></h3>'
    )
    parts.append(
        f"<p>Similarity score: <strong>{group.evidence.score}</strong> "
        f"out of {sum(FIELD_WEIGHTS.values())} possible points.</p>"
    )
    parts.append(_render_evidence(group.evidence))
    for note in group.notes:
        parts.append(f'<p class="note">{_escape(note)}</p>')
    parts.append('<div class="wrap"><table><thead><tr>')
    parts.extend(f"<th>{_escape(column)}</th>" for column in TABLE_COLUMNS)
    parts.append("</tr></thead><tbody>")
    parts.extend(_render_file_row(item) for item in group.files)
    parts.append("</tbody></table></div></section>")
    return "".join(parts)


def _render_section(level: str, groups: Sequence[MatchGroup]) -> str:
    parts = [f"<h2>{_escape(SECTION_TITLES[level])}</h2>"]
    parts.append(f"<p>{_escape(SECTION_INTROS[level])}</p>")
    if not groups:
        parts.append('<p class="empty">No groups found at this level.</p>')
        return "".join(parts)
    parts.extend(_render_group(index, group) for index, group in enumerate(groups, start=1))
    return "".join(parts)


def _render_skipped(skipped: Sequence[SkippedFile]) -> str:
    parts = ["<h2>Errors and skipped files</h2>"]
    if not skipped:
        parts.append('<p class="empty">No files were skipped and no errors were recorded.</p>')
        return "".join(parts)
    parts.append('<div class="wrap"><table><thead><tr><th>File</th><th>Reason</th></tr></thead><tbody>')
    for entry in skipped:
        parts.append(f"<tr><td>{_escape(entry.title)}</td><td>{_escape(entry.reason)}</td></tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_html_report(
    target_user: str,
    requesting_user: str,
    stats: RunStats,
    groups: Sequence[MatchGroup],
    skipped: Sequence[SkippedFile],
    generated_at: datetime | None = None,
) -> str:
    """Render the complete standalone report. Every inserted value is escaped."""
    logger.info("Generating HTML report...")
    moment = generated_at if generated_at is not None else datetime.now(timezone.utc)
    by_level: dict[str, list[MatchGroup]] = defaultdict(list)
    for group in groups:
        by_level[group.level].append(group)

    summary = [
        ("Target user", target_user),
        ("Requesting user", requesting_user),
        ("Generated at", moment.strftime("%Y-%m-%d %H:%M:%S %Z") or moment.isoformat()),
        ("Files retrieved", stats.total_files),
        ("Files with metadata", stats.files_with_metadata),
        ("Files without usable EXIF", stats.files_without_exif),
        ("Files with contradictory timestamps", stats.files_with_conflicting_timestamps),
        ("API requests", stats.api_requests),
        ("Cache hits", stats.cache_hits),
        ("Errors and skipped files", len(skipped)),
        ("Groups reported", len(groups)),
    ]

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Possible duplicate uploads by {_escape(target_user)}</title>",
        f"<style>{REPORT_CSS}</style></head><body>",
        f"<h1>Possible duplicate uploads by {_escape(target_user)}</h1>",
        '<p class="subtitle">Generated from Commons file metadata and EXIF only. '
        "No images or thumbnails were downloaded.</p>",
        '<div class="caution"><p><strong>These are candidates for manual review, not '
        "proof of duplication.</strong></p><p>Identical EXIF does not mean identical "
        "content. A camera shooting in burst mode produces several genuinely different "
        "photographs that share the same second-rounded capture timestamp, ISO, "
        "aperture, shutter speed, focal length, body and lens. Only the exact binary "
        "duplicate section below is certain.</p><p>Open every file and compare it "
        "visually before drawing any conclusion. Do not use this report to nominate or "
        "tag files automatically.</p></div>",
        '<dl class="summary">',
    ]
    for label, value in summary:
        parts.append(f"<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>")
    parts.append("</dl>")

    for level in ("sha1", "source", "level1", "level2"):
        parts.append(_render_section(level, by_level.get(level, [])))
    for level in ("level3", "level4"):
        if by_level.get(level):
            parts.append(_render_section(level, by_level[level]))
    if not any(by_level.get(level) for level in ("level3", "level4")):
        parts.append(
            "<h2>Burst sequences and related frames</h2><p>Groups that describe a "
            "sequence of different frames rather than one repeated photograph were left "
            "out. Re-run with <code>--include-series</code> to see them.</p>"
        )

    parts.append(_render_skipped(skipped))
    parts.append(
        "<footer><p>Scoring weights: "
        + _escape(", ".join(f"{FIELD_LABELS[name]} +{weight}" for name, weight in FIELD_WEIGHTS.items()))
        + ". A nearly matching capture timestamp scores half of its weight.</p>"
        f"<p>Produced by {_escape(TOOL_NAME)} {_escape(TOOL_VERSION)}, a read-only tool. "
        "It made no changes to Wikimedia Commons.</p></footer>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def _file_to_json(item: CommonsFile) -> dict[str, Any]:
    normalized = item.normalized
    return {
        "title": item.title,
        "pageid": item.pageid,
        "page_url": item.page_url,
        "upload_timestamp": item.upload_timestamp,
        "sha1": item.sha1,
        "width": item.width,
        "height": item.height,
        "size": item.size,
        "mime": item.mime,
        "mediatype": item.mediatype,
        "normalized": {
            "capture_timestamp": (
                normalized.capture_timestamp.isoformat()
                if normalized.capture_timestamp is not None
                else None
            ),
            "capture_source": normalized.capture_source,
            "capture_status": normalized.capture_status,
            "digitized_timestamp": (
                normalized.digitized_timestamp.isoformat()
                if normalized.digitized_timestamp is not None
                else None
            ),
            "fields": {
                name: {"source": entry.source, "raw": entry.raw, "key": entry.key}
                for name, entry in sorted(normalized.fields.items())
            },
        },
        "raw_metadata": item.raw_metadata,
    }


def build_json_report(
    target_user: str,
    requesting_user: str,
    stats: RunStats,
    files: Sequence[CommonsFile],
    groups: Sequence[MatchGroup],
    skipped: Sequence[SkippedFile],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable version of the report."""
    moment = generated_at if generated_at is not None else datetime.now(timezone.utc)
    return {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION, "read_only": True},
        "target_user": target_user,
        "requesting_user": requesting_user,
        "generated_at": moment.isoformat(),
        "statistics": {
            "total_files": stats.total_files,
            "files_with_metadata": stats.files_with_metadata,
            "files_without_exif": stats.files_without_exif,
            "files_with_conflicting_timestamps": stats.files_with_conflicting_timestamps,
            "api_requests": stats.api_requests,
            "cache_hits": stats.cache_hits,
        },
        "scoring_weights": FIELD_WEIGHTS,
        "files": [_file_to_json(item) for item in files],
        "groups": [
            {
                "level": group.level,
                "classification": group.classification,
                "score": group.evidence.score,
                "matching_fields": group.evidence.matching,
                "near_matching_fields": group.evidence.near_matching,
                "differing_fields": group.evidence.differing,
                "missing_fields": group.evidence.missing,
                "notes": group.notes,
                "files": [item.title for item in group.files],
            }
            for group in groups
        ],
        "skipped": [{"title": entry.title, "reason": entry.reason} for entry in skipped],
    }


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------


def _username(value: str) -> str:
    cleaned = value.strip().replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise argparse.ArgumentTypeError("username must not be empty")
    if "|" in cleaned:
        raise argparse.ArgumentTypeError("username must not contain '|'")
    return cleaned


def _request_delay(value: str) -> float:
    try:
        delay = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from exc
    if delay < MINIMUM_REQUEST_DELAY:
        raise argparse.ArgumentTypeError(
            f"the delay must be at least {MINIMUM_REQUEST_DELAY} second to stay polite "
            "towards Wikimedia Commons"
        )
    return delay


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return number


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("value must not be negative")
    return number


def default_output_path(target_user: str) -> Path:
    """Build the default report filename from the analyzed username."""
    safe = re.sub(r"[^\w.-]+", "_", target_user, flags=re.UNICODE).strip("_") or "user"
    return Path(f"commons-duplicates-{safe}.html")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commons_duplicate_finder.py",
        description=(
            "Find likely duplicate photographs among one Wikimedia Commons user's uploads "
            "using file metadata and EXIF only. Read-only: it never edits Commons."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python commons_duplicate_finder.py \\\n"
            "      --requesting-user RequestingUserName \\\n"
            "      --target-user ExampleUser"
        ),
    )
    parser.add_argument(
        "--requesting-user",
        required=True,
        type=_username,
        metavar="NAME",
        help="Commons username of the person running the script, used in the User-Agent",
    )
    parser.add_argument(
        "--target-user",
        required=True,
        type=_username,
        metavar="NAME",
        help="Commons username whose uploaded files should be analyzed",
    )
    parser.add_argument(
        "--request-delay",
        type=_request_delay,
        default=DEFAULT_REQUEST_DELAY,
        metavar="SECONDS",
        help=f"delay between API requests (default: {DEFAULT_REQUEST_DELAY})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="HTML report path (default: commons-duplicates-<target-user>.html)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        metavar="PATH",
        help="also write the full result as JSON to this path",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not read or write the local API response cache",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        metavar="PATH",
        help=f"local cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--max-retries",
        type=_positive_int,
        default=DEFAULT_MAX_RETRIES,
        metavar="N",
        help=f"retries per request (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--initial-backoff",
        type=_non_negative_float,
        default=DEFAULT_INITIAL_BACKOFF,
        metavar="SECONDS",
        help=f"first backoff delay (default: {DEFAULT_INITIAL_BACKOFF})",
    )
    parser.add_argument(
        "--max-backoff",
        type=_non_negative_float,
        default=DEFAULT_MAX_BACKOFF,
        metavar="SECONDS",
        help=f"upper bound for backoff delays (default: {DEFAULT_MAX_BACKOFF})",
    )
    parser.add_argument(
        "--max-retry-after",
        type=_non_negative_float,
        default=DEFAULT_MAX_RETRY_AFTER,
        metavar="SECONDS",
        help=(
            "abort instead of honouring a Retry-After longer than this "
            f"(default: {DEFAULT_MAX_RETRY_AFTER})"
        ),
    )
    parser.add_argument(
        "--near-timestamp-seconds",
        type=_positive_int,
        default=DEFAULT_NEAR_TIMESTAMP_SECONDS,
        metavar="SECONDS",
        help=(
            "tolerance for the weakest grouping level "
            f"(default: {DEFAULT_NEAR_TIMESTAMP_SECONDS})"
        ),
    )
    parser.add_argument(
        "--include-series",
        action="store_true",
        help=(
            "also report burst sequences and consecutive frames (levels 3 and 4, plus "
            "groups whose metered brightness shows they are different frames). Off by "
            "default, because those are series rather than repeated uploads"
        ),
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        metavar="N",
        help="analyze at most N files, useful for a quick trial run",
    )
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )


def analyze(
    client: CommonsApiClient,
    target_user: str,
    near_timestamp_seconds: int,
    limit: int | None,
    include_series: bool = False,
) -> tuple[list[CommonsFile], list[MatchGroup], list[SkippedFile], RunStats]:
    """Run the full read-only pipeline and return everything the reports need."""
    skipped: list[SkippedFile] = []
    files = fetch_user_files(client, target_user, limit)
    stats = RunStats(total_files=len(files))
    if not files:
        stats.api_requests = client.request_count
        stats.cache_hits = client.cache_hits
        return files, [], skipped, stats

    fetch_metadata(client, files, skipped)

    logger.info("Normalizing metadata...")
    analyzable: list[CommonsFile] = []
    for item in files:
        if item.mediatype and item.mediatype.upper() not in {"BITMAP", "DRAWING", "UNKNOWN", ""}:
            skipped.append(SkippedFile(item.title, f"not an image (media type {item.mediatype})"))
            continue
        item.normalized = normalize_metadata(item.raw_metadata)
        if item.normalized.has_usable_exif:
            stats.files_with_metadata += 1
        else:
            stats.files_without_exif += 1
        if item.normalized.has_contradicted_capture:
            stats.files_with_conflicting_timestamps += 1
        analyzable.append(item)

    groups = build_groups(analyzable, near_timestamp_seconds, include_series)
    stats.api_requests = client.request_count
    stats.cache_hits = client.cache_hits
    return files, groups, skipped, stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    cache = None if args.no_cache else ResponseCache(args.cache_dir)
    client = CommonsApiClient(
        user_agent=build_user_agent(args.requesting_user),
        request_delay=args.request_delay,
        retry_policy=RetryPolicy(
            max_retries=args.max_retries,
            initial_backoff=args.initial_backoff,
            max_backoff=args.max_backoff,
            max_retry_after=args.max_retry_after,
        ),
        cache=cache,
    )

    try:
        files, groups, skipped, stats = analyze(
            client,
            args.target_user,
            args.near_timestamp_seconds,
            args.limit,
            args.include_series,
        )
    except CommonsApiError as exc:
        logger.error("Fatal: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted before the report could be written.")
        return 1

    if not files:
        logger.error("Fatal: no files found for user %s.", args.target_user)
        return 1

    output_path = args.output if args.output is not None else default_output_path(args.target_user)
    document = render_html_report(args.target_user, args.requesting_user, stats, groups, skipped)
    try:
        output_path.write_text(document, encoding="utf-8")
    except OSError as exc:
        logger.error("Fatal: could not write %s: %s", output_path, exc)
        return 1
    logger.info("Wrote %s", output_path)

    if args.json_output is not None:
        payload = build_json_report(
            args.target_user, args.requesting_user, stats, files, groups, skipped
        )
        try:
            args.json_output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Fatal: could not write %s: %s", args.json_output, exc)
            return 1
        logger.info("Wrote %s", args.json_output)

    logger.info(
        "Done. %d files, %d groups, %d API requests, %d skipped.",
        stats.total_files,
        len(groups),
        stats.api_requests,
        len(skipped),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
