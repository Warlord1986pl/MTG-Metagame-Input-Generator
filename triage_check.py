"""Piątkowy alarm triage'u: python triage_check.py [--as-of RRRR-MM-DD] [--no-toast]. Kod wyjścia 0 complete,
1 pending_notice, 2 pending_blocking, 3 błąd. Opis w docs/runner.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from weekly_runner.triage_check import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
