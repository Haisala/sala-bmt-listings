"""TEST_ONLY Intake image preview contract and exact same-batch byte dedup."""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="issue39-intake-media-") as folder:
        root = Path(folder)
        os.environ.update({
            "BMT_ENV": "test",
            "BMT_ADMIN_PRIVATE_ROOT": str(root / "private"),
            "BMT_ADMIN_PREVIEW_ROOT": str(root / "preview"),
            "BMT_ADMIN_PUBLIC_ASSETS_ROOT": str(root / "assets"),
            "BMT_ADMIN_DB_PATH": str(root / "private" / "property-memory.sqlite3"),
            "BMT_OPENAI_CONTENT_LIVE": "0",
            "BMT_OPENAI_EXTRACTION": "0",
            "BMT_RUNTIME_LOCK": "0",
        })
        import admin.admin_server as adm

        adm.ensure_private()

        def create(label: str, files: list[tuple[str, bytes]], *, failure=None, supplied_key: str | None = None):
            return adm.create_intake_record(
                {"source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY " + label},
                files,
                supplied_key=supplied_key or ("TEST_ONLY-" + label + "-" + uuid.uuid4().hex),
                failure=failure,
            )

        seven = [(f"IMG_{i}.jpg", f"TEST_ONLY-image-{i}".encode()) for i in range(1, 7)]
        seven.insert(1, ("duplicate-copy.webp", seven[0][1]))
        seven_record, replayed, seven_upload = create("seven-one-duplicate", seven)
        assert not replayed
        assert seven_upload == {
            "received": 7, "accepted": 6, "duplicate_count": 1,
            "duplicates": ["duplicate-copy.webp"],
        }
        assert len(seven_record["media"]) == 6
        assert all((adm.PRIVATE_ROOT / item["path"]).read_bytes() == next(
            data for name, data in seven if name == item["filename"]
        ) for item in seven_record["media"])

        same_name_same_bytes, _, same_name_upload = create(
            "same-name-exact", [("IMG_3143.jpeg", b"TEST_ONLY-same"), ("IMG_3143.jpeg", b"TEST_ONLY-same")]
        )
        assert len(same_name_same_bytes["media"]) == 1 and same_name_upload["duplicate_count"] == 1

        same_name_different_bytes, _, _ = create(
            "same-name-different", [("same.jpg", b"TEST_ONLY-first"), ("same.jpg", b"TEST_ONLY-second")]
        )
        stored = same_name_different_bytes["media"]
        assert len(stored) == 2 and len({x["storedFilename"] for x in stored}) == 2
        assert [ (adm.PRIVATE_ROOT / x["path"]).read_bytes() for x in stored ] == [b"TEST_ONLY-first", b"TEST_ONLY-second"]

        all_duplicate, _, all_upload = create(
            "all-same", [(f"copy-{i}.png", b"TEST_ONLY-one-image") for i in range(4)]
        )
        assert len(all_duplicate["media"]) == 1 and all_upload["duplicate_count"] == 3

        mixed, _, mixed_upload = create(
            "mixed", [("a.jpg", b"TEST_ONLY-photo"), ("b.jpg", b"TEST_ONLY-photo"), ("source.txt", b"TEST_ONLY-note-file")]
        )
        assert len(mixed["media"]) == 2 and mixed_upload["duplicate_count"] == 1

        try:
            create("nonimage-repeat", [("a.txt", b"TEST_ONLY-text"), ("b.txt", b"TEST_ONLY-text")])
            raise AssertionError("same-batch non-image duplicate must retain previous 409 behavior")
        except adm.IntakeConflict as exc:
            assert exc.code == "DUPLICATE_MEDIA"

        unique, _, _ = create("existing-source", [("existing.png", b"TEST_ONLY-across-intakes")])
        try:
            create("existing-cross-intake", [("new-name.jpg", b"TEST_ONLY-across-intakes")])
            raise AssertionError("duplicate bytes from another intake must remain blocked")
        except adm.IntakeConflict as exc:
            assert exc.code == "DUPLICATE_MEDIA" and unique["id"] in str(exc)

        retry_files = [("retry.jpg", b"TEST_ONLY-retry-bytes")]
        retry_key = "TEST_ONLY-stable-retry-key"
        def fail_before_index(stage: str) -> None:
            if stage == "before_index_commit":
                raise OSError("TEST_ONLY injected storage failure")
        try:
            create("retry", retry_files, supplied_key=retry_key, failure=fail_before_index)
            raise AssertionError("injected failure should stop before index commit")
        except OSError:
            pass
        assert not any(x.get("note") == "TEST_ONLY retry" for x in adm.load_index())
        assert not list(adm.INBOX_ROOT.glob(".*.pending"))
        retry, replayed, retry_upload = create("retry", retry_files, supplied_key=retry_key)
        replay, is_replay, replay_upload = create("retry", retry_files, supplied_key=retry_key)
        assert not replayed and is_replay and replay["id"] == retry["id"]
        assert retry_upload["accepted"] == replay_upload["accepted"] == 1

        page = adm.page().decode("utf-8")
        for required in ("intake-media-input", "intake-media-list", "intake-preview", "previewDialog.showModal",
                         "URL.revokeObjectURL", "data.delete('media')", "data.append('media'", "Bỏ ảnh/tệp",
                         "window.crypto.subtle.digest", "duplicate_count"):
            assert required in page, required
        assert 'name="cover"' not in page.lower() and "reorder" not in page.lower()
        print("INTAKE_MEDIA_DEDUP_PASS: same-batch image SHA dedup, first-kept, same-name collision safety, non-image/cross-intake conflicts, failure cleanup/retry contract, preview/remove/add FormData wiring")


if __name__ == "__main__":
    main()
