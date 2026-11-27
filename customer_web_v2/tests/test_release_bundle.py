import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from deploy.build_release_bundle import ALLOWLIST, MANIFEST_NAME, build_release_bundle


class ReleaseBundleTests(unittest.TestCase):
    def test_deterministic_bundle_contains_only_explicit_runtime_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = build_release_bundle(root / "first", source_revision="TEST_ONLY-source-sha")
            second = build_release_bundle(root / "second", source_revision="TEST_ONLY-source-sha")
            first_path, second_path = Path(first["artifact"]), Path(second["artifact"])
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(first["artifact_sha256"], hashlib.sha256(first_path.read_bytes()).hexdigest())
            expected = {line for line in ALLOWLIST.read_text(encoding="utf-8").splitlines() if line.strip()}
            with zipfile.ZipFile(first_path) as archive:
                self.assertEqual(set(archive.namelist()), expected | {MANIFEST_NAME})
                manifest = json.loads(archive.read(MANIFEST_NAME))
                self.assertEqual(manifest["source_revision"], "TEST_ONLY-source-sha")
                for entry in manifest["files"]:
                    self.assertEqual(hashlib.sha256(archive.read(entry["path"])).hexdigest(), entry["sha256"])
                names = set(archive.namelist())
                self.assertFalse(any(name.endswith((".sqlite", ".sqlite3", ".db", ".pem", ".key")) for name in names))
                self.assertFalse(any(part in {"private", "media", "logs", ".git", "__pycache__"} for name in names for part in Path(name).parts))
            sidecar = json.loads(Path(str(first_path) + ".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["artifact_sha256"], first["artifact_sha256"])
            self.assertEqual(first_path.stat().st_mode & 0o777, 0o600)

    def test_refuses_output_inside_source_tree(self):
        from deploy.build_release_bundle import ROOT
        with self.assertRaisesRegex(ValueError, "RELEASE_OUTPUT_MUST_BE_OUTSIDE_SOURCE"):
            build_release_bundle(ROOT / "customer_web_v2" / "release-test-only")


if __name__ == "__main__":
    unittest.main()
