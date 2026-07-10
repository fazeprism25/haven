"""Dev-only script: fully regenerate Haven's demo data.

Seeding is already a full clear-and-regenerate (see
``scripts/seed_demo.py``'s ``main()``), so "reset" is exactly that, under a
more discoverable name for demo/rehearsal use -- e.g. after a live "Remember"
call or manual edits during a run-through leave the vault in a state you
don't want to keep.

Usage:
    python scripts/reset_demo.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_demo import main  # noqa: E402

if __name__ == "__main__":
    main()
