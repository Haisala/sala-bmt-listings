"""Encrypted TEST_ONLY SQLite + media snapshot bundles with hash manifest."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

MAGIC = b"SALA-BDS-SNAPSHOT-V1\n"
ITERATIONS = 200_000


def _passphrase(value: bytes | str) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(raw, bytes) or len(raw) < 32 or b"\n" in raw or b"\r" in raw:
        raise ValueError("SNAPSHOT_PASSPHRASE_INVALID")
    return raw


def _private_root(path: Path, *, create: bool) -> Path:
    if path.is_symlink():
        raise ValueError("SNAPSHOT_SYMLINK_REJECTED")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise PermissionError("SNAPSHOT_ROOT_NOT_PRIVATE")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise PermissionError("SNAPSHOT_ROOT_NOT_PRIVATE")
    return path.resolve()


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
        raise ValueError("SNAPSHOT_PATH_UNSAFE")
    return path


def _regular(source_root: Path, relative: PurePosixPath) -> Path:
    path = source_root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError("SNAPSHOT_SOURCE_NOT_REGULAR")
    resolved = path.resolve(strict=True)
    resolved.relative_to(source_root)
    if resolved.stat().st_uid != os.getuid():
        raise PermissionError("SNAPSHOT_SOURCE_OWNER_INVALID")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise PermissionError("SNAPSHOT_SOURCE_NOT_PRIVATE")
    return resolved


def _sqlite_snapshot(source: Path, destination: Path) -> dict:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
        integrity = dst.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("SNAPSHOT_SQLITE_INTEGRITY_FAILED")
        tables = [row[0] for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {name: int(dst.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in tables}
        columns = {row[1] for row in dst.execute("PRAGMA table_info(schema_meta)")} if "schema_meta" in tables else set()
        version = dst.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()[0] if "version" in columns else None
        return {"integrity": "ok", "schema_version": version, "table_counts": counts}
    finally:
        dst.close()
        src.close()


def _openssl() -> str:
    executable = shutil.which("openssl")
    if not executable:
        raise RuntimeError("SNAPSHOT_OPENSSL_UNAVAILABLE")
    return executable


def _encrypt(plain: Path, encrypted: Path, passphrase: bytes, private_dir: Path) -> None:
    openssl = _openssl()
    secret_path = private_dir / (".snapshot-pass-" + uuid.uuid4().hex)
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(passphrase + b"\n")
        result = subprocess.run([
            openssl, "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", str(ITERATIONS),
            "-pass", f"file:{secret_path}", "-in", str(plain), "-out", str(encrypted),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30)
        if result.returncode != 0:
            raise RuntimeError("SNAPSHOT_ENCRYPTION_FAILED")
    finally:
        secret_path.unlink(missing_ok=True)


def _decrypt(encrypted: Path, plain: Path, passphrase: bytes, private_dir: Path) -> None:
    openssl = _openssl()
    secret_path = private_dir / (".snapshot-pass-" + uuid.uuid4().hex)
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(passphrase + b"\n")
        result = subprocess.run([
            openssl, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", str(ITERATIONS),
            "-pass", f"file:{secret_path}", "-in", str(encrypted), "-out", str(plain),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30)
        if result.returncode != 0:
            raise ValueError("SNAPSHOT_DECRYPTION_FAILED")
    finally:
        secret_path.unlink(missing_ok=True)


def create_snapshot(source_root: str | Path, relative_paths: list[str], database_path: str,
                    destination: str | Path, passphrase: bytes | str) -> dict:
    source = _private_root(Path(source_root).expanduser(), create=False)
    secret = _passphrase(passphrase)
    db_rel = _relative(database_path)
    paths = [_relative(value) for value in relative_paths]
    if len(paths) != len(set(paths)) or db_rel not in paths:
        raise ValueError("SNAPSHOT_FILE_LIST_INVALID")
    source_files = {rel.as_posix(): _regular(source, rel) for rel in paths}
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(target.name)
    parent = _private_root(target.parent, create=True)
    if target.parent.resolve() != parent:
        raise ValueError("SNAPSHOT_TARGET_UNSAFE")
    with tempfile.TemporaryDirectory(prefix=".sala-snapshot-", dir=parent) as folder:
        stage = Path(folder)
        content = stage / "content"
        content.mkdir(mode=0o700)
        entries = []
        sqlite_metadata = None
        for relative in sorted(source_files):
            rel = PurePosixPath(relative)
            output = content.joinpath(*rel.parts)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if rel == db_rel:
                sqlite_metadata = _sqlite_snapshot(source_files[relative], output)
            else:
                shutil.copyfile(source_files[relative], output)
            output.chmod(0o600)
            data = output.read_bytes()
            entries.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"schema": "SALA_BDS_DATA_MEDIA_SNAPSHOT_V1", "database": db_rel.as_posix(),
                    "database_metadata": sqlite_metadata, "files": entries}
        manifest_path = content / "snapshot-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        manifest_path.chmod(0o600)
        plain = stage / "snapshot.tar"
        with tarfile.open(plain, "w") as archive:
            for relative in sorted(source_files):
                archive.add(content.joinpath(*PurePosixPath(relative).parts), arcname=relative, recursive=False)
            archive.add(manifest_path, arcname="snapshot-manifest.json", recursive=False)
        if plain.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("SNAPSHOT_SIZE_LIMIT")
        encrypted = stage / "snapshot.enc"
        _encrypt(plain, encrypted, secret, parent)
        salt = os.urandom(16)
        ciphertext = encrypted.read_bytes()
        derived = hashlib.pbkdf2_hmac("sha256", secret, salt, ITERATIONS, dklen=64)
        tag = hmac.new(derived[32:], MAGIC + salt + ciphertext, hashlib.sha256).digest()
        payload = MAGIC + ITERATIONS.to_bytes(4, "big") + salt + tag + ciphertext
        temp_target = stage / "final.enc"
        temp_target.write_bytes(payload)
        temp_target.chmod(0o600)
        os.replace(temp_target, target)
        target.chmod(0o600)
        return {"snapshot": str(target), "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "file_count": len(entries), "table_counts": sqlite_metadata["table_counts"]}


def restore_snapshot(snapshot_path: str | Path, destination: str | Path, passphrase: bytes | str) -> dict:
    snapshot = Path(snapshot_path).expanduser()
    if snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_mode & 0o077:
        raise PermissionError("SNAPSHOT_SOURCE_NOT_PRIVATE")
    secret = _passphrase(passphrase)
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(target.name)
    parent = _private_root(target.parent, create=True)
    if target.parent.resolve() != parent:
        raise ValueError("SNAPSHOT_TARGET_UNSAFE")
    if snapshot.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("SNAPSHOT_SIZE_LIMIT")
    data = snapshot.read_bytes()
    header_len = len(MAGIC) + 4 + 16 + 32
    if len(data) <= header_len or not data.startswith(MAGIC):
        raise ValueError("SNAPSHOT_FORMAT_INVALID")
    offset = len(MAGIC)
    iterations = int.from_bytes(data[offset:offset + 4], "big")
    if iterations != ITERATIONS:
        raise ValueError("SNAPSHOT_KDF_INVALID")
    salt = data[offset + 4:offset + 20]
    tag = data[offset + 20:offset + 52]
    ciphertext = data[offset + 52:]
    derived = hashlib.pbkdf2_hmac("sha256", secret, salt, iterations, dklen=64)
    expected = hmac.new(derived[32:], MAGIC + salt + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("SNAPSHOT_AUTHENTICATION_FAILED")
    temp_dir = Path(tempfile.mkdtemp(prefix=".sala-restore-", dir=parent))
    temp_dir.chmod(0o700)
    try:
        encrypted = temp_dir / "ciphertext"
        encrypted.write_bytes(ciphertext)
        plain = temp_dir / "snapshot.tar"
        _decrypt(encrypted, plain, secret, temp_dir)
        stage = temp_dir / "extracted"
        stage.mkdir(mode=0o700)
        with tarfile.open(plain, "r:") as archive:
            members = archive.getmembers()
            if len(members) > 5000 or sum(item.size for item in members) > 512 * 1024 * 1024:
                raise ValueError("SNAPSHOT_EXPANSION_LIMIT")
            for member in members:
                rel = _relative(member.name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("SNAPSHOT_MEMBER_UNSAFE")
                output = stage.joinpath(*rel.parts)
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source_handle = archive.extractfile(member)
                if source_handle is None:
                    raise ValueError("SNAPSHOT_MEMBER_INVALID")
                with source_handle, output.open("xb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
                output.chmod(0o600)
        manifest_path = stage / "snapshot-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "SALA_BDS_DATA_MEDIA_SNAPSHOT_V1":
            raise ValueError("SNAPSHOT_MANIFEST_INVALID")
        expected_paths = {entry["path"] for entry in manifest.get("files", [])}
        if not isinstance(manifest.get("files"), list) or not manifest["files"] or len(expected_paths) != len(manifest["files"]):
            raise ValueError("SNAPSHOT_MANIFEST_INVALID")
        actual_paths = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file() and p != manifest_path}
        if actual_paths != expected_paths:
            raise ValueError("SNAPSHOT_FILE_SET_MISMATCH")
        for entry in manifest["files"]:
            path = stage.joinpath(*_relative(entry["path"]).parts)
            content = path.read_bytes()
            if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("SNAPSHOT_FILE_HASH_MISMATCH")
        db_path = stage.joinpath(*_relative(manifest["database"]).parts)
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SNAPSHOT_SQLITE_INTEGRITY_FAILED")
            for table, count in manifest["database_metadata"]["table_counts"].items():
                actual = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if actual != count:
                    raise ValueError("SNAPSHOT_SQLITE_COUNT_MISMATCH")
        finally:
            db.close()
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        os.replace(stage, target)
        return {"destination": str(target), "manifest_sha256": manifest_hash,
                "file_count": len(manifest["files"]), "table_counts": manifest["database_metadata"]["table_counts"]}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
