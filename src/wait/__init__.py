"""Wait skill implementation."""

import sys
from pathlib import Path

dependencies = Path(__file__).resolve().parents[1] / "_deps"
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))
