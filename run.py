"""Start the dashboard from any working directory: `python run.py` (same as `python -m homeenergy serve`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from homeenergy.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["serve"]))
