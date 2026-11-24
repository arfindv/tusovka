"""Бекап бази й обкладинок: python backup.py  → instance/backups/tusovka-<час>.db та .zip (14 останніх)."""
import glob
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

from tusovka import create_app

KEEP = 14


def main():
    app = create_app()
    db_path, upload_dir = app.config["DATABASE_PATH"], app.config["UPLOAD_DIR"]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # sqlite backup API: узгоджена копія навіть під час роботи сервера (WAL)
    target = os.path.join(out_dir, f"tusovka-{stamp}.db")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    shutil.make_archive(os.path.join(out_dir, f"uploads-{stamp}"), "zip", upload_dir)

    for pattern in ("tusovka-*.db", "uploads-*.zip"):
        for old in sorted(glob.glob(os.path.join(out_dir, pattern)))[:-KEEP]:
            os.remove(old)
    print(f"Бекап збережено: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
