# Scripts

A collection of useful scripts and instructions for everyday development tasks.

---

## Table of Contents

- [Git](#git)
  - [Clone All Repositories](#clone-all-repositories)
  - [Flatten Branch History](#flatten-branch-history)
- [PDF](#pdf)
  - [Images to PDF](#images-to-pdf)
  - [Unlock PDF](#unlock-pdf)
- [Varia](#varia)
  - [Download Files from Webpage](#download-files-from-webpage)
  - [Refresh USB Disks by Reading](#refresh-usb-disks-by-reading)
  - [Markdown to PDF](#markdown-to-pdf)
- [Wikimedia](#wikimedia)
  - [Commons Duplicate Finder](#commons-duplicate-finder)
- [Commands](#commands)
- [Instructions](#instructions)

---

## Git

Shell scripts for batch operations on multiple local repositories:

| Script | Description |
|--------|-------------|
| [`git/currentBranchInfoAll.sh`](git/currentBranchInfoAll.sh) | Print current branch for every git repository in the current directory |
| [`git/flatten_branch_history.sh`](git/flatten_branch_history.sh) | Replace a branch history with one new commit after an explicit safety confirmation |
| [`git/mainBranchAndPull.sh`](git/mainBranchAndPull.sh) | Switch to `main` (or `master`) branch and run `git pull` for every git repository found at any depth below the given directory (default: current) |
| [`git/pullAll.sh`](git/pullAll.sh) | Run `git pull` on every git repository in the current directory |

Windows CMD equivalents (in `git/windows_cmd/`):

| Script | Description |
|--------|-------------|
| [`git/windows_cmd/pull_all_repos_from_subdirs.bat`](git/windows_cmd/pull_all_repos_from_subdirs.bat) | `git pull origin` on every subdirectory |
| [`git/windows_cmd/all_repos_from_subdirs_checkout_master_branch.bat`](git/windows_cmd/all_repos_from_subdirs_checkout_master_branch.bat) | Checkout `master` branch in every subdirectory |

### Clone All Repositories

[`git/clone_all_repos.py`](git/clone_all_repos.py) — clone **all** GitHub repositories
owned by the authenticated user into an organized directory structure.
Repositories that already exist in the target directory are skipped, so the
script is safe to run repeatedly (e.g. to pick up newly created repos).

```
<target_dir>/
├── public/       # public repositories
├── private/      # private repositories
│   └── shared/   # private repos shared with outside collaborators
└── profile/      # the user's profile repo (username/username)
```

**Setup:**

1. Create a fine-grained personal access token at
   <https://github.com/settings/tokens>
2. Token configuration:
   - **Repository Access** → All repositories
   - **Permissions** → Contents: *read-only*, Administration: *read-only*
3. Save the token as `GITHUB_API_KEY` in a `.env` file

**Usage:**

```bash
pip install requests python-dotenv
python git/clone_all_repos.py USERNAME [TARGET_DIR]          # SSH (default)
python git/clone_all_repos.py USERNAME [TARGET_DIR] --https  # HTTPS with token
```

### Flatten Branch History

[`git/flatten_branch_history.sh`](git/flatten_branch_history.sh) — replace the complete history of a selected branch with one new commit containing the current files, then push the rewritten branch to the remote with `--force-with-lease`.

This is a destructive history rewrite. The script prints a warning and requires typing `REPLACE HISTORY` before it runs. For non-interactive automation, `--yes` is accepted only when `FLATTEN_BRANCH_HISTORY_CONFIRM=YES` is also set.

**Usage:**

```bash
bash git/flatten_branch_history.sh main
bash git/flatten_branch_history.sh main --message "Initial commit"
bash git/flatten_branch_history.sh main --remote origin
FLATTEN_BRANCH_HISTORY_CONFIRM=YES bash git/flatten_branch_history.sh main --yes
```

**Parameters:**

- `<branch>` (required): branch whose history will be replaced, for example `main` or `master`
- `-m`, `--message` (optional): commit message for the new single commit
- `-r`, `--remote` (optional, default: `origin`): remote repository name used for fetch and push
- `-y`, `--yes` (optional): skip the prompt only when `FLATTEN_BRANCH_HISTORY_CONFIRM=YES` is set

---

## PDF

| Script | Description |
|--------|-------------|
| [`pdf/images_to_pdf.py`](pdf/images_to_pdf.py) | Combine all images from a directory into a single PDF, one image per A4 page |
| [`pdf/unlock_pdf.py`](pdf/unlock_pdf.py) | Remove password protection from a PDF file and extract its embedded attachments |

### Images to PDF

[`pdf/images_to_pdf.py`](pdf/images_to_pdf.py) — convert **all** `.jpg`, `.jpeg`, and `.png` images from a directory into one PDF file.
Images are sorted alphabetically (case-insensitive) and each is placed on a separate A4 portrait page.
Each image is scaled to the maximum size that fits the page while preserving its aspect ratio, then centered.
Corrupted or unreadable files are skipped with a warning.

**Setup:**

```bash
pip install Pillow reportlab
```

**Usage:**

```bash
python3 pdf/images_to_pdf.py                              # images from current directory → output.pdf
python3 pdf/images_to_pdf.py --dir /path/to/images        # images from specified directory → output.pdf
python3 pdf/images_to_pdf.py --dir /path/to/images --output result.pdf
```

### Unlock PDF

[`pdf/unlock_pdf.py`](pdf/unlock_pdf.py) — remove password protection from a PDF file.
Prompts for the password securely (no terminal echo) and saves the decrypted content to a new file
with an `unlocked_` prefix in the same directory as the original.

If the source PDF contains embedded files, they are extracted as well: both document-level
attachments and file attachment annotations are saved as separate files next to the output PDF,
each named with the output filename as prefix.
The attachments also stay embedded in the unlocked PDF.
Names taken from the PDF are reduced to a safe filename, and existing files are never overwritten —
a `_1`, `_2`, … suffix is added instead.

**Setup:**

```bash
pip install pikepdf
```

**Usage:**

```bash
python3 pdf/unlock_pdf.py protected.pdf            # → unlocked_protected.pdf
python3 pdf/unlock_pdf.py /path/to/protected.pdf   # → /path/to/unlocked_protected.pdf
```

**Example with attachments:**

```bash
python3 pdf/unlock_pdf.py report.pdf
# Unlocked PDF saved to: unlocked_report.pdf
# Attachment saved to: unlocked_report_invoice.xml
# Attachment saved to: unlocked_report_annex.pdf
```

---

## Varia

| Script | Description |
|--------|-------------|
| [`varia/download_files_from_webpage.sh`](varia/download_files_from_webpage.sh) | Download media files from a webpage, especially useful for plain "index of" directory listings |
| [`varia/md_to_pdf.py`](varia/md_to_pdf.py) | Merge all Markdown files from a directory into a single PDF, sorted alphabetically by filename |
| [`varia/refresh_usb_disks_by_reading.sh`](varia/refresh_usb_disks_by_reading.sh) | Read all detected USB disks (without writing) to help surface read errors, with per-pass logs |


### Download Files from Webpage

[`varia/download_files_from_webpage.sh`](varia/download_files_from_webpage.sh) — download linked files from a webpage (for example simple directory listings such as "index of").
The script resolves absolute and relative links, skips HTML/PHP pages and directory links, decodes URL-encoded filenames, sanitizes invalid filename characters, and retries failed downloads.

**Requirements:**

- `bash`
- `wget`
- `grep` with Perl-regex support (`-P`)
- `sed`, `sort`, `basename`, `cut`

**Usage:**

```bash
bash varia/download_files_from_webpage.sh -u <PAGE_URL> [-o <OUTPUT_DIR>] [-r <RETRIES>] [-d <DELAY_SECONDS>]
```

**Parameters:**

- `-u` (required): page URL containing links to files to download
- `-o` (optional, default: `.`): output directory
- `-r` (optional, default: `10`): number of retries per file
- `-d` (optional, default: `30`): delay (seconds) between retry attempts

**Examples:**

```bash
# Download to current directory
bash varia/download_files_from_webpage.sh -u 'https://example.com/files/'

# Download to a custom directory with fewer retries
bash varia/download_files_from_webpage.sh -u 'https://example.com/files/' -o ./downloads -r 5 -d 15
```

### Refresh USB Disks by Reading

[`varia/refresh_usb_disks_by_reading.sh`](varia/refresh_usb_disks_by_reading.sh) — detect connected USB disks and read each full device one or more times (`dd` to `/dev/null`) to help identify read issues. The script requires root, asks for explicit `YES` confirmation, writes no data to disks, and creates a log file for each disk/pass.

**Usage:**

```bash
sudo bash varia/refresh_usb_disks_by_reading.sh [passes]
```

- `passes` (optional, default: `2`): integer from `1` to `10`

### Markdown to PDF

[`varia/md_to_pdf.py`](varia/md_to_pdf.py) — convert **all** `.md` files from a directory into one PDF file.
Files are included in alphabetical order (case-insensitive sort by filename).
Supports non-English content (Polish, German, etc.) using Unicode-aware fonts.
Invalid or unreadable files are skipped with a warning; the script exits with an error if no valid files are found.

**Setup:**

```bash
pip install markdown weasyprint
```

**Usage:**

```bash
python3 varia/md_to_pdf.py OUTPUT.pdf                    # .md files from current directory
python3 varia/md_to_pdf.py --dir /path/to/docs OUTPUT.pdf  # .md files from specified directory
```

---

## Wikimedia

| Script | Description |
|--------|-------------|
| [`wikimedia/commons_duplicates/commons_duplicate_finder.py`](wikimedia/commons_duplicates/commons_duplicate_finder.py) | Find likely duplicate photographs among one Wikimedia Commons user's uploads, using file metadata and EXIF only |

### Commons Duplicate Finder

[`wikimedia/commons_duplicates/commons_duplicate_finder.py`](wikimedia/commons_duplicates/commons_duplicate_finder.py) — list **all** files uploaded to
Wikimedia Commons by a chosen user, group the ones whose EXIF metadata is identical or nearly
identical, and write a standalone HTML report of the suspicious groups.
The script is strictly **read-only**: it only sends anonymous `GET` requests to the public MediaWiki
API and never edits Commons, nominates files or adds templates.
It uses metadata and EXIF only — no images or thumbnails are downloaded, no visual comparison is
performed and no perceptual hashes are calculated, so every group it reports is a candidate for
manual review rather than a confirmed duplicate.
Requests are sequential and rate-limited (at least one second apart), `429` responses honour
`Retry-After`, and temporary failures use bounded exponential backoff.

**Setup:**

```bash
pip install -r wikimedia/commons_duplicates/requirements.txt
```

**Usage:**

```bash
python3 wikimedia/commons_duplicates/commons_duplicate_finder.py \
    --requesting-user RequestingUserName \
    --target-user ExampleUser                        # → commons-duplicates-ExampleUser.html
```

**Parameters:**

- `--requesting-user` (required): your own Commons username, used to build an identifiable `User-Agent`
- `--target-user` (required): the Commons username whose uploads are analyzed
- `--request-delay` (optional, default: `1.0`): seconds between API requests, minimum `1.0`
- `--output` (optional): HTML report path
- `--json-output` (optional): also write the full result as JSON
- `--include-series` (optional): also report burst sequences and consecutive frames, which are
  hidden by default because they are series rather than repeated uploads

See [`wikimedia/commons_duplicates/README.md`](wikimedia/commons_duplicates/README.md) for the full
parameter list, the grouping levels, the scoring weights, caching and the limitations of a
metadata-only approach.

---

## Commands

Handy one-liners collected in [`commands.md`](commands.md).

---

## Instructions

Step-by-step guides:

- [Copy code between git repositories with preserved history](instructions/copy_between_git_repos_with_history.md)

---
