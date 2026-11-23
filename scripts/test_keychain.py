#!/usr/bin/env python3
"""TEST_ONLY Keychain resolver status and sanitized failure mapping."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from property_memory.keychain import CredentialError, read_macos_keychain, resolve_openai_key


def main() -> None:
    def missing(*_args, **_kwargs):
        raise subprocess.CalledProcessError(44, ["security"], stderr="item not found")

    try:
        read_macos_keychain(runner=missing)
        raise AssertionError("missing credential unexpectedly resolved")
    except CredentialError as exc:
        assert exc.code == "KEYCHAIN_CREDENTIAL_MISSING" and exc.os_status == 44

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["security"], timeout=10)

    try:
        read_macos_keychain(runner=timeout)
        raise AssertionError("timeout unexpectedly resolved")
    except CredentialError as exc:
        assert exc.code == "KEYCHAIN_ACCESS_TIMEOUT" and exc.os_status is None

    def ready(*_args, **_kwargs):
        return SimpleNamespace(stdout="TEST_ONLY_value")

    assert read_macos_keychain(runner=ready) == "TEST_ONLY_value"
    try:
        resolve_openai_key(keychain_reader=lambda: (_ for _ in ()).throw(CredentialError("KEYCHAIN_CREDENTIAL_MISSING", 44)),
                           environ={"OPENAI_API_KEY": "TEST_ONLY_env_must_not_fallback"}, allow_env_fallback=False)
        raise AssertionError("credential resolver used the disabled environment fallback")
    except CredentialError as exc:
        assert exc.code == "KEYCHAIN_CREDENTIAL_MISSING" and exc.os_status == 44

    print("KEYCHAIN_TEST_PASS: TEST_ONLY success, OSStatus 44 mapping, bounded timeout mapping, ENV fallback disabled")


if __name__ == "__main__":
    main()
