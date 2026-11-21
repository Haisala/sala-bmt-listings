"""Create and verify private per-service runtime directories."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def prepare(root: Path) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise PermissionError("Runtime root must be a real directory owned by the service user")
    root.chmod(0o700)
    children = tuple(root / name for name in ("data", "backups", "logs"))
    for child in children:
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_symlink() or child.stat().st_uid != os.getuid():
            raise PermissionError("Runtime directory must be owned by the service user")
        child.chmod(0o700)
        if child.stat().st_mode & 0o077:
            raise PermissionError("Runtime directory permissions are not private")
    return children  # data, backups, logs


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare private Customer Web runtime directories")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    prepare(args.root)
    print("RUNTIME_DIRS PASS; data/backups/logs directories are owner-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
