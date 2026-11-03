"""Entry point of the desktop app, frozen or run from a venv (#192).

Stdlib only, and never named app.py (that would shadow the backend's `app`
package). `python desktop/launcher.py` from a venv with the backend
installed runs exactly what the frozen executable runs.
"""

import multiprocessing
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if not getattr(sys, "frozen", False):
        here = Path(__file__).resolve().parent
        sys.path[:0] = [str(here), str(here.parent / "backend")]
    from gt7_desktop.main import main

    sys.exit(main(sys.argv[1:]))
