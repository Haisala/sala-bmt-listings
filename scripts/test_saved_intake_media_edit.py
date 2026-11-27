"""TEST_ONLY real-HTTP coverage for Owner edits to saved intake photos."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def png(color: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    width, height = 640, 480
    row = b"\x00" + bytes(color) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def multipart(fields: dict[str, str], files: list[tuple[str, bytes]], boundary: str) -> bytes:
    out = bytearray()
    for key, value in fields.items():
        out.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    for name, payload in files:
        out.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n".encode())
        out.extend(payload + b"\r\n")
    out.extend(f"--{boundary}--\r\n".encode())
    return bytes(out)


def call(url: str, *, cookie: str = "", origin: str = "", fields: dict | None = None,
         files: list[tuple[str, bytes]] | None = None, boundary: str = "TEST_ONLY-boundary",
         expected: int | None = None) -> tuple[int, dict, bytes]:
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if cookie:
        headers["Cookie"] = cookie
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=multipart(fields or {}, files or [], boundary), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        result = exc.code, dict(exc.headers), exc.read()
    if expected is not None:
        assert result[0] == expected, (result[0], result[2][:300])
    return result


def main() -> None:
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory(prefix="issue39-media-edit-") as temp:
        root = Path(temp); root.chmod(0o700)
        private = root / "private"; private.mkdir(mode=0o700)
        env = {"BMT_ENV": "test", "BMT_ADMIN_LOCAL_HTTP_DEV": "1", "BMT_ADMIN_PRIVATE_ROOT": str(private),
               "BMT_ADMIN_PREVIEW_ROOT": str(root / "preview"), "BMT_ADMIN_PUBLIC_ASSETS_ROOT": str(root / "assets"),
               "BMT_ADMIN_DB_PATH": str(private / "property-memory.sqlite3"), "BMT_ADMIN_PUBLIC_ORIGIN": "",
               "BMT_ADMIN_OWNER_ID": "TEST_ONLY-owner", "BMT_OPENAI_CONTENT_LIVE": "0", "BMT_OPENAI_EXTRACTION": "0",
               "BMT_RUNTIME_LOCK": "0", "BMT_TEST_ONLY": "1"}
        with patch.dict(os.environ, env):
            import admin.admin_server as admin
            from property_memory.review_center import ReviewCenter
            from property_memory.store import PropertyStore
            admin.PRIVATE_ROOT = private; admin.INBOX_ROOT = private / "inbox"
            admin.INDEX_PATH = private / "index.json"; admin.AUDIT_LOG_PATH = private / "audit.jsonl"
            admin.ERROR_QUEUE_PATH = private / "error-queue.json"
            admin.ensure_private()
            admin.REVIEW_CENTER = None
            colors = [(i * 19 % 255, i * 37 % 255, i * 59 % 255) for i in range(1, 11)]
            originals = [(f"TEST_ONLY-{i}.png", png(color)) for i, color in enumerate(colors, 1)]
            intake, replay, _ = admin.create_intake_record(
                {"source": "TEST_ONLY", "sourceGroup": "SYNTHETIC", "note": "TEST_ONLY media editing"},
                originals, supplied_key="TEST_ONLY-media-edit-seed")
            assert not replay and len(intake["media"]) == 10
            session = "TEST_ONLY-media-edit-session"
            admin.SESSIONS[session] = datetime.now(timezone.utc).timestamp() + 300
            admin.SESSION_OWNERS[session] = "TEST_ONLY-owner"
            server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
            admin.PORT = server.server_port
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            url = origin + f"/api/intakes/{intake['id']}/media-edit"
            cookie = f"bmt_admin_session={session}"
            timings = {}
            try:
                status, _, _ = call(url, origin=origin, fields={"action": "add", "expected_media_revision": "1"},
                                    files=[("TEST_ONLY-unauth.png", png((1, 2, 3)))], expected=401)
                status, _, _ = call(url, cookie=cookie, origin="https://attacker.invalid",
                                    fields={"action": "add", "expected_media_revision": "1"},
                                    files=[("TEST_ONLY-csrf.png", png((2, 3, 4)))], expected=403)
                for item in intake["media"]:
                    req = urllib.request.Request(origin + f"/api/intakes/{intake['id']}/media/{item['mediaId']}", headers={"Cookie": cookie})
                    with urllib.request.urlopen(req, timeout=4) as response:
                        assert response.status == 200 and response.read() == originals[int(item["filename"].split("-")[1].split(".")[0]) - 1][1]
                assert len(intake["media"]) == 10

                first = intake["media"][0]; rev = 1
                started = time.perf_counter()
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "remove", "expected_media_revision": str(rev), "media_id": first["mediaId"]}, expected=200)
                timings["remove_ms"] = round((time.perf_counter() - started) * 1000, 1)
                removed = json.loads(body); rev = removed["media_revision"]
                assert len(removed["media"]) == 9 and (private / first["path"]).is_file()

                target = removed["media"][0]; replacement = png((240, 111, 55))
                started = time.perf_counter()
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "replace", "expected_media_revision": str(rev), "media_id": target["mediaId"]},
                    files=[("TEST_ONLY-replacement.png", replacement)], expected=200)
                timings["replace_ms"] = round((time.perf_counter() - started) * 1000, 1)
                replaced = json.loads(body); rev = replaced["media_revision"]
                replacement_row = next(item for item in replaced["media"] if item["filename"] == "TEST_ONLY-replacement.png")
                assert len(replaced["media"]) == 9 and replacement_row["mediaId"] != target["mediaId"]
                assert replacement_row["sha256"] == hashlib.sha256(replacement).hexdigest()
                assert (private / target["path"]).is_file()

                additions = [(f"TEST_ONLY-add-{i}.png", png((i, 200 - i, 80 + i))) for i in (11, 12)]
                started = time.perf_counter()
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "add", "expected_media_revision": str(rev)}, files=additions, expected=200)
                timings["add_two_ms"] = round((time.perf_counter() - started) * 1000, 1)
                added = json.loads(body); rev = added["media_revision"]
                assert len(added["media"]) == 11 and all(x["rights_status"] == "UNCONFIRMED" for x in added["media"] if x["mediaId"] in added["media"][-2:])
                assert len({x["mediaId"] for x in added["media"]}) == 11

                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "add", "expected_media_revision": str(rev)},
                    files=[("TEST_ONLY-readd-original.png", originals[0][1])], expected=409)
                assert json.loads(body)["error"] == "DUPLICATE_MEDIA"

                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "add", "expected_media_revision": str(rev)},
                    files=[("TEST_ONLY-invalid.jpg", b"not-an-image")], expected=400)
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "remove", "expected_media_revision": "1", "media_id": added["media"][0]["mediaId"]}, expected=409)
                persisted = next(row for row in admin.load_index() if row["id"] == intake["id"])
                metadata = json.loads((private / "inbox" / intake["id"] / "metadata.json").read_text())
                assert len(persisted["media"]) == len(metadata["media"]) == 11
                assert persisted["mediaRevision"] == metadata["mediaRevision"] == 4
                assert len(persisted["mediaHistory"]) == 2 and len(persisted["mediaSelectionHistory"]) == 3
                assert len({row["mediaId"] for row in persisted["media"]}) == 11
                assert all((private / row["path"]).is_file() for row in persisted["media"] + persisted["mediaHistory"])
                assert not any(row.get("published") for row in persisted.get("media", []))

                store = PropertyStore(private / "property-memory.sqlite3")
                center = ReviewCenter(store)
                package = center.create_intake_package({"candidate_id": "TEST_ONLY-candidate", "intake_id": intake["id"], "media": persisted["media"]}, [])
                assert store.list_properties() == []
                live_package = center.packages[package["review_id"]]
                live_package["content_batches"] = [{"review_revision": live_package["review_revision"], "variants": [{"content_variant_id": "TEST_ONLY-variant"}]}]
                live_package["selected_content_variant_id"] = "TEST_ONLY-variant"
                live_package["selected_content"] = {"content_variant_id": "TEST_ONLY-variant"}
                center._persist(live_package); admin.REVIEW_CENTER = center
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "remove", "expected_media_revision": str(rev), "media_id": persisted["media"][0]["mediaId"]}, expected=200)
                rev = json.loads(body)["media_revision"]
                assert live_package["review_revision"] == 2 and live_package["state"] == "EDITED"
                assert live_package["media_revision"] == live_package["candidate"]["media_revision"] == rev
                assert live_package["candidate"]["media"] == json.loads((private / "index.json").read_text())[0]["media"]
                assert len(live_package["original_candidate"]["media"]) == 11
                assert len(live_package["candidate"]["media"]) == 10
                assert "selected_content_variant_id" not in live_package and "selected_content" not in live_package
                assert live_package["content_batches"][0]["media_revision_stale"] is True
                live_package["state"] = "APPROVED"; center._persist(live_package)
                admin.REVIEW_CENTER = center
                _, _, body = call(url, cookie=cookie, origin=origin,
                    fields={"action": "remove", "expected_media_revision": str(rev), "media_id": persisted["media"][0]["mediaId"]}, expected=409)
                assert json.loads(body)["error"] == "MEDIA_EDIT_GATE_A_PROTECTED"
                assert len(next(row for row in admin.load_index() if row["id"] == intake["id"])["media"]) == 10
                assert store.list_properties() == []
                page_req = urllib.request.Request(origin + "/", headers={"Cookie": cookie})
                with urllib.request.urlopen(page_req, timeout=4) as response:
                    page = response.read().decode("utf-8")
                for expected in ("media-editor", "Xóa", "Thay ảnh", "Thêm ảnh", "naturalWidth", "await img.decode()", "preview tải được"):
                    assert expected in page, expected
                print("SAVED_INTAKE_MEDIA_HTTP_TEST_ONLY_PASS: 10 source images HTTP 200; remove 10→9; replace 9→9 with new media identity; add two 9→11; exact index/metadata reload; source lineage retained; duplicate IDs absent; invalid MIME bytes 400; auth 401; hostile origin 403; stale revision 409; Gate A edit blocked 409; no provider/publication calls; timings_ms=" + json.dumps(timings, sort_keys=True))
            finally:
                server.shutdown(); thread.join(timeout=4); server.server_close()
                admin.SESSIONS.pop(session, None); admin.SESSION_OWNERS.pop(session, None)


if __name__ == "__main__":
    main()
