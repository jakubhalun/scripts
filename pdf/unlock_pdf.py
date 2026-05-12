#!/usr/bin/env python3
# Commands:
# pip install pikepdf
# python3 pdf/unlock_pdf.py protected.pdf
# python3 pdf/unlock_pdf.py /path/to/protected.pdf

"""Remove password protection from a PDF file.

Opens the encrypted PDF using the provided password and saves a new copy
without any encryption. The output filename is derived automatically by
prepending 'unlocked_' to the original filename.

Usage:
    python3 pdf/unlock_pdf.py <input_pdf>
"""

import getpass
import os
import sys

import pikepdf


def unlock_pdf(input_path: str, output_path: str) -> None:
    """Open a password-protected PDF and save it without encryption.

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
    except pikepdf.PasswordError:
        print("Error: incorrect password.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: could not process '{input_path}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Unlocked PDF saved to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 unlock_pdf.py <input_pdf>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    directory = os.path.dirname(input_file)
    basename = os.path.basename(input_file)
    output_file = os.path.join(directory, f"unlocked_{basename}") if directory else f"unlocked_{basename}"

    unlock_pdf(input_file, output_file)
