import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from deploy.runtime_dirs import prepare
from sala_web import Application


class DeployConfigTests(unittest.TestCase):
    def test_runtime_dirs_are_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Customer Web"
            data, backups, logs = prepare(root)
            for path in (root, data, backups, logs):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_launch_agent_is_disabled_by_default_and_scrubs_inherited_secret(self):
        plist_path = Path(__file__).parents[1] / "deploy" / "mac" / "com.haisala.customer-web.plist.example"
        with plist_path.open("rb") as stream:
            job = plistlib.load(stream)
        args = job["ProgramArguments"]
        self.assertEqual(args[1], "-i")
        self.assertFalse(job["RunAtLoad"])
        self.assertTrue(job["KeepAlive"])
        self.assertIn("SALA_PORT=18877", args)
        self.assertIn("SALA_BIND_HOST=127.0.0.1", args)
        self.assertFalse(any("SALA_OWNER_PASSWORD" in value for value in args))
        self.assertNotIn("EnvironmentVariables", job)

    def test_missing_owner_credential_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "private" / "empty.sqlite3", None, {"https://haisala.io.vn"})
            login = app.handle("POST", "/api/login", {"Origin": "https://haisala.io.vn", "Content-Type": "application/json"}, b'{"password":"TEST_ONLY"}')
            self.assertEqual(login.status, 401)
            self.assertEqual(app.handle("GET", "/api/admin/pending").status, 401)


if __name__ == "__main__":
    unittest.main()
