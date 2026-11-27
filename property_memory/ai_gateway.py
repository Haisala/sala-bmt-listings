"""Disabled-by-default, deterministic-safe boundary around the existing AI provider."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from .intelligence import IntelligenceResult


class AIGatewayError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class GatewayResult:
    execution_id: str
    status: str
    result: IntelligenceResult | None
    error_code: str | None = None


class AIGateway:
    def __init__(self, *, provider: Any, enabled: bool = False, allowed_models: set[str] | None = None,
                 model: str = "gpt-6-luna", max_requests: int = 0, rate_window_seconds: float = 60.0,
                 timeout_seconds: float = 30.0, kill_switch: bool = False):
        self.provider = provider
        self.enabled = bool(enabled)
        self.allowed_models = allowed_models or set()
        self.model = model
        self.max_requests = int(max_requests)
        self.rate_window_seconds = float(rate_window_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.kill_switch = bool(kill_switch)
        self._lock = threading.RLock()
        self._reservations: dict[str, float] = {}
        self._replays: dict[str, GatewayResult] = {}
        self._calls: list[float] = []
        self._failures = 0

    def _execution_id(self, intake: dict[str, Any], operation_id: str) -> str:
        return "ai-" + hashlib.sha256((operation_id + ":" + str(intake.get("id") or intake.get("intake_id"))).encode()).hexdigest()[:24]

    def analyze(self, intake: dict[str, Any], *, operation_id: str) -> GatewayResult:
        execution_id = self._execution_id(intake, operation_id)
        with self._lock:
            if execution_id in self._replays:
                return self._replays[execution_id]
            if not self.enabled:
                result = GatewayResult(execution_id, "DISABLED", None, "AI_DISABLED")
                self._replays[execution_id] = result
                return result
            if self.kill_switch:
                raise AIGatewayError("AI_KILL_SWITCH")
            if self.model not in self.allowed_models:
                raise AIGatewayError("AI_MODEL_NOT_ALLOWED")
            now = time.monotonic()
            self._calls = [stamp for stamp in self._calls if now - stamp < self.rate_window_seconds]
            if self.max_requests <= 0 or len(self._calls) + len(self._reservations) >= self.max_requests:
                raise AIGatewayError("AI_BUDGET_EXHAUSTED")
            self._reservations[execution_id] = now
        try:
            value = self.provider.extract_validated(intake)
            result = GatewayResult(execution_id, "SUCCEEDED", value, None)
            with self._lock:
                self._calls.append(time.monotonic())
                self._replays[execution_id] = result
            return result
        except Exception as exc:
            code = getattr(exc, "code", None) or str(exc)[:80] or "AI_FAILED"
            with self._lock:
                self._failures += 1
            raise AIGatewayError("AI_FAILED:" + code, diagnostics=getattr(exc, "diagnostics", None)) from None
        finally:
            with self._lock:
                self._reservations.pop(execution_id, None)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {"enabled": self.enabled, "model_allowed": self.model in self.allowed_models,
                    "active_reservations": len(self._reservations), "completed_calls": len(self._calls),
                    "failures": self._failures, "replay_entries": len(self._replays)}
