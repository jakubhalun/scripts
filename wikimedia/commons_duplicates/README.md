# Commons duplicate finder

Find likely duplicate photographs among the files uploaded to Wikimedia Commons by one user,
using **file metadata and EXIF only**.

The script retrieves the user's current uploads through the public MediaWiki API, normalizes the
relevant EXIF fields, groups files whose metadata is identical or nearly identical, and writes a
standalone HTML report listing the suspicious groups.

**The report is a list of candidates for manual review. It is not proof that any two files are
duplicates.** Only the exact binary duplicate section is certain.

## Read-only

This script is strictly read-only. It performs anonymous HTTP `GET` requests against
`https://commons.wikimedia.org/w/api.php` and nothing else. It never logs in, never requests an
edit token, and never sends a write action. It does not edit Commons, does not nominate files for
deletion, does not add templates, and does not leave messages anywhere.

## Installation

Requires **Python 3.11 or newer**. The only dependency is `requests`.

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows PowerShell:**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Usage

```bash
python commons_duplicate_finder.py \
    --requesting-user RequestingUserName \
    --target-user ExampleUser
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--requesting-user` | *required* | Your own Commons username. Used only to build an identifiable `User-Agent` so Wikimedia can see who is making the requests. |
| `--target-user` | *required* | The Commons username whose uploads are analyzed. May differ from `--requesting-user`. |
| `--request-delay` | `1.0` | Seconds to wait between API requests. Values below `1.0` are rejected. |
| `--output` | `commons-duplicates-<target-user>.html` | Path of the HTML report. |
| `--json-output` | *(none)* | Also write the full result as JSON to this path. |
| `--no-cache` | off | Do not read or write the local response cache. |
| `--cache-dir` | `.cache/commons-duplicate-finder` | Where to keep cached API responses. |
| `--max-retries` | `5` | Retries per request before giving up. |
| `--initial-backoff` | `2.0` | First backoff delay, in seconds. |
| `--max-backoff` | `60.0` | Upper bound for backoff delays, in seconds. |
| `--max-retry-after` | `300` | Abort rather than honour a `Retry-After` longer than this. |
| `--near-timestamp-seconds` | `1` | Timestamp tolerance for the weakest grouping level. |
| `--include-series` | off | Also report burst sequences and consecutive frames (levels 3 and 4, and groups whose metered brightness shows they are different frames). |
| `--limit` | *(none)* | Analyze at most N files. Useful for a quick trial run. |
| `--verbose` | off | Enable debug logging. |

Usernames may contain spaces, underscores, Unicode characters and URL-special characters.
Underscores and spaces are treated as equivalent, and the name is URL-encoded where needed.

### Examples

```bash
# Default run
python commons_duplicate_finder.py --requesting-user Me --target-user ExampleUser

# Slower requests, custom report location, plus a JSON dump
python commons_duplicate_finder.py \
    --requesting-user Me \
    --target-user ExampleUser \
    --request-delay 1.5 \
    --output report.html \
    --json-output report.json

# Include burst sequences and consecutive frames as well
python commons_duplicate_finder.py \
    --requesting-user Me \
    --target-user ExampleUser \
    --include-series

# Quick trial on the first 50 files, ignoring the cache
python commons_duplicate_finder.py \
    --requesting-user Me \
    --target-user ExampleUser \
    --limit 50 --no-cache --verbose
```

## Request throttling

Being polite towards Wikimedia matters more here than being fast.

- All requests are **sequential**. There are no threads, no async I/O, no process pools.
- One reusable `requests.Session` is shared by every request.
- A delay of at least `--request-delay` seconds separates every pair of requests, including retries.
- The upload list is fetched 500 files per request; metadata is fetched in batches of 50 titles.
  A user with 2000 uploads therefore costs roughly 45 requests rather than 2000.
- Connection timeout is 10 seconds, read timeout 60 seconds.

## Retry behaviour

| Situation | Behaviour |
|-----------|-----------|
| `429 Too Many Requests` | Honour `Retry-After`, whether it holds a number of seconds or an HTTP date. If the header is missing or unparseable, fall back to exponential backoff. A small random jitter is added. |
| `Retry-After` above `--max-retry-after` | Abort with a clear message instead of sleeping for a very long time. |
| `5xx` responses | Bounded exponential backoff with jitter: 2s, 4s, 8s, ... capped at `--max-backoff`. |
| Connection timeouts, resets, incomplete responses | Same bounded exponential backoff. |
| Other `4xx` responses | Not retried. These are permanent client errors. |
| MediaWiki `error` object in a `200` response | Not retried; reported as a fatal error. |

Every retry is logged. Retries stop after `--max-retries` attempts.

## Metadata grouping logic

The capture timestamp is taken from the strongest available EXIF tag, in this order:
`DateTimeOriginal`, `DateTimeDigitized`, another EXIF capture tag, then the generic `DateTime`.
**The Commons upload timestamp is never used as the capture timestamp**; it appears in the report
as a separate column.

### Cross-checking the capture timestamp

Editing software regularly rewrites one of the EXIF timestamps. Photoshop Elements in particular
can copy a stale `DateTimeOriginal` into unrelated exports, which makes photographs taken weeks
apart look like they were shot in the same second. Each file's `DateTimeOriginal` is therefore
checked against its own `DateTimeDigitized`:

| Relationship | Verdict | Effect on grouping |
|--------------|---------|--------------------|
| The two tags are equal | corroborated | Normal grouping |
| They differ by an exact whole number of hours, up to 26 | timezone offset — the same instant written in two timezones | Normal grouping, so the same photo exported by two different tools still groups |
| No `DateTimeDigitized` | unchecked | Normal grouping |
| They differ by anything else, for example a day or a month | **contradicted** | `DateTimeDigitized` becomes part of the file's capture identity, and the file is excluded from level 4 chaining |

A contradicted file can still group, but only with files that carry the *same* contradiction. Files
whose `DateTimeOriginal` collides purely because software rewrote it no longer land in one group.
Any group containing a contradicted file carries an explicit note, the report header counts them,
and the affected rows show the conflicting `DateTimeDigitized` value under the capture timestamp.

Values are normalized before comparison: whitespace is collapsed, camera names are compared
case-insensitively, underscores are treated as spaces, rationals such as `10/1250` become `1/125`,
ISO becomes an integer, and focal length and aperture become plain numbers. APEX `ShutterSpeedValue`
and `ApertureValue` are converted to seconds and f-numbers. The original raw values are kept and
shown in the report.

Grouping runs on indexes rather than an all-pairs comparison, so it stays usable for users with
thousands of uploads. Files with no capture timestamp are never grouped.

| Section | Grouped on | Classification | Shown by default |
|---------|-----------|----------------|------------------|
| SHA-1 | Identical Commons SHA-1 hash | `Exact binary duplicate` | yes |
| Source image | Identical XMP source image identifier | `Same source image` | yes |
| Level 1 | Capture timestamp, camera make and model, lens, exposure, aperture, ISO, focal length | `Very strong metadata match` | yes |
| Level 2 | The same minus the lens, so a file with no lens metadata still groups | `Strong metadata match` | yes |
| Level 3 | Capture timestamp, camera make and model | `Possible duplicate or burst sequence` | only with `--include-series` |
| Level 4 | Timestamps within `--near-timestamp-seconds` while all exposure metadata matches exactly | `Possible related frames` | only with `--include-series` |

Dimensions are deliberately **not** part of any key, so a crop or a resized version still groups
with its original. Files are never grouped merely because they were taken on the same day.

### Duplicates versus series

The goal is to find one photograph uploaded more than once, not a run of consecutive frames.
Levels 3 and 4 describe a sequence by construction — a camera firing twice a second produces frames
whose timestamps differ by one second and whose settings are identical — so they are **withheld
unless you pass `--include-series`**.

Two further mechanisms separate repeated uploads from separate frames:

- **Source image identifier.** XMP `OriginalDocumentID` names the image an export was derived from.
  Two exports of one photograph share it; two frames do not. It survives cropping and re-encoding,
  which makes it the strongest signal after an identical hash. Different crops of one original also
  share it, so such a group can legitimately hold different pictures cut from the same frame.
  Identifiers shared by more than ten files are ignored: some phones write a single constant value
  into everything they produce, which identifies the camera rather than the image.
- **Metered brightness.** `BrightnessValue` is a measurement rather than a setting, so two frames of
  one scene almost always differ while two exports of one frame agree. When two files in a group
  both report it and the values differ, the group describes different frames and is treated as a
  series. A missing value proves nothing, so this can only ever split a group, never create one.

Levels are evaluated from strongest to weakest, and a weaker group is reported only when it
connects files that no stronger group already reported together. The same set therefore never
appears in four sections at once. A group with an unusually large number of files is flagged,
because that usually means a placeholder capture timestamp rather than duplicated content.

### Similarity score

Every group carries a score built from plain, published weights, so any number in the report can be
recomputed by hand:

| Field | Points |
|-------|--------|
| Identical source image identifier | +45 |
| Identical capture timestamp | +40 |
| Identical camera make and model | +15 |
| Identical camera serial number | +10 |
| Identical lens model | +10 |
| Identical exposure time | +8 |
| Identical aperture | +8 |
| Identical ISO | +8 |
| Identical focal length | +8 |
| Identical metered brightness | +5 |
| Identical orientation | +3 |

A capture timestamp that only *nearly* matches scores half its weight. A field counts as matching
only when every file in the group reports the same normalized value. If any file lacks the field it
is listed as **missing** rather than as a difference, because absent EXIF is not evidence either
way. Each group lists its matching, nearly matching, differing and missing fields.

A level 3 or level 4 group scoring below 60 is relabelled `Weak match`.

## Local cache

Successful API responses are cached in `.cache/commons-duplicate-finder/`, one JSON file per
request, named after a SHA-256 hash of the sorted request parameters. Each file holds the request
parameters, a fetch timestamp and the response body.

The cache is local only, contains no authentication credentials, and is safe to delete at any time.
Failed responses are never stored, so a cache hit always replays something the API really returned.
Uncached requests still go through the normal throttling. Use `--no-cache` to bypass it entirely or
`--cache-dir` to move it.

## Reading the report

The report opens with the run context: both usernames, the execution time, how many files were
retrieved, how many had usable EXIF, how many API requests were made, and how many files were
skipped. Then come the group sections, strongest evidence first, followed by the list of errors and
skipped files.

For each group the report shows the classification, the similarity score, which fields matched,
nearly matched, differed or were missing, and a table of the files with their Commons links, upload
and capture timestamps, camera, lens, exposure, aperture, ISO, focal length, dimensions, file size
and SHA-1.

Only the **exact binary duplicate** section is certain. Everything else is a candidate. Open each
file and compare it visually before drawing any conclusion.

## Limitations

This version:

- does **not** download images,
- does **not** download thumbnails,
- does **not** perform any visual comparison,
- does **not** calculate perceptual hashes,
- **cannot** reliably distinguish duplicates from burst photographs,
- **requires** human review of every group it reports.

More specifically:

- Identical EXIF does not prove that two files are duplicates.
- A camera in burst mode produces several genuinely different photographs sharing the same
  timestamp, ISO, aperture, shutter speed, focal length, body and lens.
- EXIF timestamps are usually rounded to the nearest second, so distinct frames often collide.
- Exported or edited files may have missing, rewritten or partially stripped EXIF. A rewritten
  `DateTimeOriginal` is detected only when `DateTimeDigitized` survived to contradict it; if both
  tags were rewritten together, unrelated photographs can still be grouped.
- Photographers who shoot with fixed manual settings produce many files sharing exposure time,
  aperture, ISO and focal length, so those fields carry less weight than the table implies.
- Different versions of the same photograph may carry different metadata and will then not group.
- Files without EXIF cannot be detected at all by this approach.
- Cropped or re-exported versions are missed unless they carry a source image identifier.
- Files straight from a camera carry no source image identifier, so for them the tool still relies
  on timestamps and exposure settings alone.
- The tool must never be used to nominate or tag files automatically.

## Tests

The tests cover normalization, timestamp selection, grouping, scoring, retry handling and HTML
generation. They use only the standard library and never make a real network request; all HTTP is
mocked.

From this directory:

```bash
python -m unittest discover -s tests -t .
```

From the repository root:

```bash
python -m unittest discover -s wikimedia/commons_duplicates/tests -t wikimedia/commons_duplicates
```

## Future extensibility

API access, metadata extraction, normalization, grouping, scoring and report rendering are kept
separate so that a future version could add thumbnail downloads, perceptual hashes, crop-aware
image comparison and visual previews. None of that is implemented here, and no image-processing
library is a dependency.
