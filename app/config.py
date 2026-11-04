import os
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
STATIC_DIR = Path(__file__).parent / "static"


def db_path() -> Path:
    """SQLite file. Set ZEIT_DB_PATH to a path on a volume (read at call time, tests override it)."""
    return Path(os.environ.get("ZEIT_DB_PATH", "data/zeit.db"))
