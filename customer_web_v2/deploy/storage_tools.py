"""Private SQLite backup and restore helpers for Customer Web."""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("Storage directory must be owned by the current service user")
    path.chmod(0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("Storage directory permissions are not private")
    return path


def _integrity_check(path: Path) -> None:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        version = db.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if not version or version[0] != 1:
            raise ValueError("Unsupported Customer Web schema version")
    finally:
        db.close()


def _validate_source(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Source must be a regular SQLite file")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("SQLite source must be service-user-owned and private")


def _copy_sqlite(source: Path, destination: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _atomic_copy(source: Path, destination: Path) -> Path:
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular SQLite file")
    parent = _private_dir(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination.name)
    fd, temp_name = tempfile.mkstemp(prefix=".sala-sqlite-", suffix=".tmp", dir=parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        _copy_sqlite(source, temp)
        temp.chmod(0o600)
        _integrity_check(temp)
        os.replace(temp, destination)
        destination.chmod(0o600)
        return destination
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def backup(source: Path, destination: Path) -> Path:
    _validate_source(source)
    _integrity_check(source)
    return _atomic_copy(source, destination)


def restore(backup_path: Path, destination: Path) -> Path:
    _validate_source(backup_path)
    _integrity_check(backup_path)
    return _atomic_copy(backup_path, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or restore a private Customer Web SQLite snapshot")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("backup", "restore"):
        cmd = sub.add_parser(name)
        cmd.add_argument("source", type=Path)
        cmd.add_argument("destination", type=Path)
    args = parser.parse_args()
    operation = backup if args.action == "backup" else restore
    output = operation(args.source, args.destination)
    print(f"{args.action.upper()} PASS; private SQLite file created ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
