"""Runner tygodnia ("jeden klik"): python metagame_week.py [--week 2026-W44] [--stage data|build|all] [--force]
[--test-monthly]. Opis w docs/runner.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from weekly_runner.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
