"""Filesystem locations. Everything mutable lives under data/ (gitignored)."""

from pathlib import Path

#: The BEMT folder itself (the one holding run.bat), not the python package.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "bemt.db"
CONFIG_PATH = DATA_DIR / "config.json"
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
