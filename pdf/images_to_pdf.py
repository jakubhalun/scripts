#!/usr/bin/env python3
# Commands:
# pip install Pillow reportlab
# python3 pdf/images_to_pdf.py
# python3 pdf/images_to_pdf.py --dir /path/to/images
# python3 pdf/images_to_pdf.py --dir /path/to/images --output result.pdf

"""Convert all images in a directory to a single PDF, one image per A4 page.

Supported formats: .jpg, .jpeg, .png (case-insensitive).
Images are sorted alphabetically and scaled to fit A4 while preserving aspect ratio.
Each image is centered on its page. Corrupted or unreadable files are skipped.
"""

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_OUTPUT = "output.pdf"


def collect_images(directory: Path) -> list[Path]:
    return sorted(
        (p for p in directory.iterdir() if p.suffix.casefold() in IMAGE_EXTENSIONS),
        key=lambda p: p.name.casefold(),
    )


def fit_dimensions(
    img_w: float, img_h: float, page_w: float, page_h: float
) -> tuple[float, float]:
    scale = min(page_w / img_w, page_h / img_h)
    return img_w * scale, img_h * scale


def to_image_reader(img: Image.Image, source_path: Path) -> ImageReader:
    """Return an ImageReader with compression preserved (JPEG for photos, PNG otherwise)."""
    fmt = "JPEG" if source_path.suffix.casefold() in {".jpg", ".jpeg"} else "PNG"
    if img.mode not in ("RGB", "RGBA") and fmt == "JPEG":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    buf.seek(0)
    return ImageReader(buf)


def draw_image_on_page(c: canvas.Canvas, image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            img_w, img_h = img.size
            image_reader = to_image_reader(img, image_path)
    except Exception as e:
        print(f"  Warning: cannot open '{image_path.name}': {e}", file=sys.stderr)
        return False

    page_w, page_h = A4
    draw_w, draw_h = fit_dimensions(float(img_w), float(img_h), page_w, page_h)
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2

    try:
        c.drawImage(image_reader, x, y, width=draw_w, height=draw_h)
    except Exception as e:
        print(f"  Warning: cannot render '{image_path.name}': {e}", file=sys.stderr)
        return False

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert all images in a directory to a single PDF (one image per A4 page)."
    )
    parser.add_argument(
        "--dir",
        default=".",
        metavar="DIRECTORY",
        help="Directory containing images (default: current directory)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        metavar="FILE",
        help=f"Output PDF file (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    directory = Path(args.dir).resolve()
    output = Path(args.output)

    if not directory.is_dir():
        print(f"Error: '{args.dir}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    images = collect_images(directory)
    if not images:
        print(f"No images found in '{directory}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(images)} image(s) in '{directory}':")

    c = canvas.Canvas(str(output), pagesize=A4)
    drawn = 0
    skipped: list[str] = []

    for image_path in images:
        print(f"  Processing: {image_path.name}")
        if draw_image_on_page(c, image_path):
            c.showPage()
            drawn += 1
        else:
            skipped.append(image_path.name)

    if skipped:
        print(f"\nSkipped {len(skipped)} image(s) due to errors:")
        for name in skipped:
            print(f"  - {name}")

    if drawn == 0:
        print("Error: no valid images to convert.", file=sys.stderr)
        sys.exit(1)

    c.save()
    print(f"\nPDF written to: {output}  ({drawn} page(s))")


if __name__ == "__main__":
    main()
