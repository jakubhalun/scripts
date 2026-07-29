#!/usr/bin/env python3
# Commands:
# pip install pikepdf
# python3 pdf/unlock_pdf.py protected.pdf
# python3 pdf/unlock_pdf.py /path/to/protected.pdf

"""Remove password protection from a PDF file.

Opens the encrypted PDF using the provided password and saves a new copy
without any encryption. The output filename is derived automatically by
prepending 'unlocked_' to the original filename.

Files embedded in the source PDF (document-level attachments and file
attachment annotations) are extracted as well. Each one is written next to the
output file, with the output filename as prefix, for example
'unlocked_report_invoice.xml'.

Usage:
    python3 pdf/unlock_pdf.py <input_pdf>
"""

import getpass
import os
import re
import sys

import pikepdf

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str) -> str:
    """Reduce an embedded file name to a safe base filename.

    Embedded names may carry directory components or characters that are not
    allowed in filenames, so only the last path segment is kept and cleaned.
    """
    candidate = name.replace("\\", "/").split("/")[-1]
    candidate = INVALID_FILENAME_CHARS.sub("_", candidate).strip(" .")
    return candidate or fallback


def embedded_file_stream(filespec) -> pikepdf.Object | None:
    """Return the stream holding the embedded data of a file specification."""
    if not isinstance(filespec, pikepdf.Dictionary):
        return None

    embedded_files = filespec.get("/EF")
    if not isinstance(embedded_files, pikepdf.Dictionary):
        return None

    for key in ("/UF", "/F"):
        stream = embedded_files.get(key)
        if isinstance(stream, pikepdf.Stream):
            return stream
    return None


def embedded_file_name(filespec, fallback: str) -> str:
    """Return the name declared by a file specification, if it has one."""
    for key in ("/UF", "/F"):
        value = filespec.get(key)
        if value is not None:
            return sanitize_filename(str(value), fallback)
    return fallback


def document_filespecs(pdf: pikepdf.Pdf):
    """Yield file specifications from the document-level attachment tree."""
    names = pdf.Root.get("/Names")
    if not isinstance(names, pikepdf.Dictionary):
        return

    embedded_files = names.get("/EmbeddedFiles")
    if not isinstance(embedded_files, pikepdf.Dictionary):
        return

    for _key, filespec in pikepdf.NameTree(embedded_files).items():
        yield filespec


def annotation_filespecs(pdf: pikepdf.Pdf):
    """Yield file specifications attached to pages as annotations."""
    for page in pdf.pages:
        annotations = page.get("/Annots")
        if not isinstance(annotations, pikepdf.Array):
            continue

        for annotation in annotations:
            if not isinstance(annotation, pikepdf.Dictionary):
                continue
            if annotation.get("/Subtype") != pikepdf.Name.FileAttachment:
                continue

            filespec = annotation.get("/FS")
            if filespec is not None:
                yield filespec


def collect_attachments(pdf: pikepdf.Pdf) -> list[tuple[str, bytes]]:
    """Collect all files embedded in the PDF as (filename, content) pairs.

    The same file can be referenced both by the attachment tree and by an
    annotation, so streams already collected are skipped.
    """
    attachments: list[tuple[str, bytes]] = []
    seen_streams = set()

    filespecs = list(document_filespecs(pdf)) + list(annotation_filespecs(pdf))
    for index, filespec in enumerate(filespecs, start=1):
        stream = embedded_file_stream(filespec)
        if stream is None:
            continue

        objgen = stream.objgen
        if objgen != (0, 0):
            if objgen in seen_streams:
                continue
            seen_streams.add(objgen)

        fallback_name = f"attachment_{index}"
        try:
            data = stream.read_bytes()
        except Exception as e:
            print(f"Warning: could not read attachment '{embedded_file_name(filespec, fallback_name)}': {e}",
                  file=sys.stderr)
            continue

        attachments.append((embedded_file_name(filespec, fallback_name), data))

    return attachments


def unique_path(path: str, taken: set) -> str:
    """Return a path that is neither already used in this run nor on disk."""
    if path not in taken and not os.path.exists(path):
        return path

    base, extension = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f"{base}_{counter}{extension}"
        if candidate not in taken and not os.path.exists(candidate):
            return candidate
        counter += 1


def save_attachments(attachments: list[tuple[str, bytes]], output_path: str) -> list[str]:
    """Write attachments next to the output file, prefixed with its name."""
    directory = os.path.dirname(output_path)
    prefix = os.path.splitext(os.path.basename(output_path))[0]

    saved_paths: list[str] = []
    taken = set()
    for name, data in attachments:
        target = os.path.join(directory, f"{prefix}_{name}") if directory else f"{prefix}_{name}"
        target = unique_path(target, taken)
        taken.add(target)

        try:
            with open(target, "wb") as attachment_file:
                attachment_file.write(data)
        except OSError as e:
            print(f"Warning: could not save attachment '{name}': {e}", file=sys.stderr)
            continue

        saved_paths.append(target)

    return saved_paths


def unlock_pdf(input_path: str, output_path: str) -> None:
    """Open a password-protected PDF and save it without encryption.

    Embedded files found in the source PDF are saved as separate files next to
    the output file.

    Args:
        input_path:  Path to the encrypted source PDF.
        output_path: Path where the unlocked PDF will be written.

    Raises:
        SystemExit: On incorrect password or any other processing error.
    """
    if not os.path.isfile(input_path):
        print(f"Error: file not found: '{input_path}'", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass(f"Password for '{os.path.basename(input_path)}': ")

    try:
        with pikepdf.open(input_path, password=password) as pdf:
            # Saving without specifying encryption removes it entirely.
            pdf.save(output_path)
            attachments = collect_attachments(pdf)
    except pikepdf.PasswordError:
        print("Error: incorrect password.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: could not process '{input_path}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Unlocked PDF saved to: {output_path}")

    for path in save_attachments(attachments, output_path):
        print(f"Attachment saved to: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 unlock_pdf.py <input_pdf>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    directory = os.path.dirname(input_file)
    basename = os.path.basename(input_file)
    output_file = os.path.join(directory, f"unlocked_{basename}") if directory else f"unlocked_{basename}"

    unlock_pdf(input_file, output_file)
