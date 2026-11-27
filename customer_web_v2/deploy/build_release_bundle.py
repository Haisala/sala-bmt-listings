"""Build a deterministic, allowlisted Customer Web service bundle (no deploy)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("release-allowlist.txt")
MANIFEST_NAME = "release-manifest.json"
FORBIDDEN_PARTS = {".git", ".env", "secrets", "secret", "credentials", "keychain", "media", "private", "logs", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"}


def _source_revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "SOURCE_REVISION_UNAVAILABLE"


def _files() -> list[tuple[str, bytes]]:
    paths = [line.strip() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("RELEASE_ALLOWLIST_INVALID")
    result = []
    for relative in sorted(paths):
        path = Path(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("RELEASE_PATH_UNSAFE")
        if any(part.lower() in FORBIDDEN_PARTS for part in path.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError("RELEASE_PATH_PRIVATE")
        source = ROOT
        for part in path.parts:
            source = source / part
            if source.is_symlink():
                raise ValueError("RELEASE_SOURCE_SYMLINK_REJECTED")
        if not source.is_file():
            raise ValueError("RELEASE_SOURCE_NOT_REGULAR")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise ValueError("RELEASE_SOURCE_INVALID")
        result.append((path.as_posix(), source.read_bytes()))
    return result


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.create_system = 3
    return info


def build_release_bundle(output_dir: str | Path, *, source_revision: str | None = None) -> dict[str, str | int]:
    requested = Path(output_dir).expanduser()
    if requested.is_symlink():
        raise ValueError("RELEASE_OUTPUT_SYMLINK_REJECTED")
    destination = requested.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("RELEASE_OUTPUT_MUST_BE_OUTSIDE_SOURCE")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.is_symlink() or stat.S_IMODE(destination.stat().st_mode) & 0o077:
        raise PermissionError("RELEASE_OUTPUT_NOT_PRIVATE")
    files = _files()
    entries = [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files]
    manifest = {"schema": "SALA_CUSTOMER_WEB_SERVICE_BUNDLE_V1", "source_revision": source_revision or _source_revision(), "files": entries}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    bundle = destination / "customer-web-service.zip"
    sidecar = destination / (bundle.name + ".manifest.json")
    if bundle.exists() or bundle.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise FileExistsError(bundle.name)
    fd, temp_name = tempfile.mkstemp(prefix=".customer-web-", suffix=".zip", dir=destination)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in files:
                archive.writestr(_zip_info(name), data)
            archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        os.replace(temp, bundle)
        bundle.chmod(0o600)
        receipt = {**manifest, "artifact": bundle.name, "artifact_sha256": digest}
        sidecar.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        sidecar.chmod(0o600)
        return {"artifact": str(bundle), "artifact_sha256": digest, "files": len(files), "source_revision": manifest["source_revision"]}
    except Exception:
        temp.unlink(missing_ok=True)
        bundle.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an allowlisted Customer Web service bundle; does not deploy")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--source-revision")
    args = parser.parse_args()
    receipt = build_release_bundle(args.output_dir, source_revision=args.source_revision)
    print("RELEASE_BUNDLE_PASS " + json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
