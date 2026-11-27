"""Fail-closed credential resolution for the dedicated macOS Keychain item."""
from __future__ import annotations
import os
import subprocess
import sys
from typing import Callable, Mapping

KEYCHAIN_SERVICE = "HaiSalaBDS.OpenAI"
KEYCHAIN_ACCOUNT = "OPENAI_API_KEY"

class CredentialError(RuntimeError):
    def __init__(self, code: str, os_status: int | None = None):
        super().__init__(code); self.code = code; self.os_status = os_status

def read_macos_keychain(*, runner: Callable[..., object] | None = None) -> str:
    if sys.platform != "darwin":
        raise CredentialError("KEYCHAIN_UNAVAILABLE")
    run = runner or subprocess.run
    try:
        result = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"], capture_output=True, text=True, check=True, timeout=10)
        value = str(getattr(result, "stdout", "")).strip()
        if not value: raise CredentialError("KEYCHAIN_CREDENTIAL_MISSING")
        return value
    except CredentialError:
        raise
    except FileNotFoundError:
        raise CredentialError("SECURITY_COMMAND_UNAVAILABLE") from None
    except subprocess.TimeoutExpired:
        raise CredentialError("KEYCHAIN_ACCESS_TIMEOUT") from None
    except subprocess.CalledProcessError as exc:
        detail = f"{getattr(exc, 'returncode', '')} {getattr(exc, 'stderr', '')}".lower()
        os_status = exc.returncode if isinstance(getattr(exc, "returncode", None), int) else None
        if "could not be found" in detail or "no such item" in detail or getattr(exc, "returncode", None) == 44:
            raise CredentialError("KEYCHAIN_CREDENTIAL_MISSING", os_status=os_status) from None
        if "denied" in detail or "not allowed" in detail or "authorization" in detail or "interaction" in detail:
            raise CredentialError("KEYCHAIN_ACCESS_DENIED", os_status=os_status) from None
        raise CredentialError("KEYCHAIN_ACCESS_FAILED", os_status=os_status) from None
    except Exception:
        raise CredentialError("KEYCHAIN_ACCESS_FAILED") from None

def resolve_openai_key(*, keychain_reader: Callable[[], str] | None = None, environ: Mapping[str, str] | None = None, allow_env_fallback: bool = True) -> str:
    """Prefer Keychain; permit an explicit environment fallback for tests/dev."""
    env = environ if environ is not None else os.environ
    try:
        return (keychain_reader or read_macos_keychain)()
    except CredentialError as exc:
        if allow_env_fallback and env.get("OPENAI_API_KEY"):
            return str(env["OPENAI_API_KEY"])
        raise exc
