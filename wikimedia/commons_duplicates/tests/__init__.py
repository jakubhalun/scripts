"""Test package for the Commons duplicate finder.

Adds the project directory to ``sys.path`` so the tests can import
``commons_duplicate_finder`` no matter which directory they are launched from.
None of the tests perform real network requests.
"""

import logging
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Retry and progress messages would otherwise flood the test output.
logging.getLogger("commons_duplicate_finder").addHandler(logging.NullHandler())
logging.getLogger("commons_duplicate_finder").propagate = False
