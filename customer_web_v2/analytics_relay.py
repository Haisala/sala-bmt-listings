"""Bounded, credential-free relay from Customer Web to Admin analytics."""
from __future__ import annotations

import json
import ipaddress
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class AnalyticsRelayError(RuntimeError):
    def __init__(self, status: int = 503):
        super().__init__("ANALYTICS_RELAY_UNAVAILABLE")
        self.status = status


class AnalyticsRelay:
    def __init__(self, endpoint: str | None, *, test_mode: bool = False, timeout: float = 2.0):
        self.endpoint = self._validate_endpoint(endpoint, test_mode=test_mode)
        self.timeout = timeout

    @staticmethod
    def _validate_endpoint(endpoint: str | None, *, test_mode: bool) -> str | None:
        if endpoint is None or not endpoint.strip():
            return None
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != "/api/analytics/events"):
            raise ValueError("ANALYTICS_RELAY_ENDPOINT_INVALID")
        if parsed.scheme != "https":
            try:
                is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                is_loopback = parsed.hostname.lower() == "localhost"
            if not test_mode or not is_loopback:
                raise ValueError("ANALYTICS_RELAY_HTTPS_REQUIRED")
        return endpoint

    def send(self, payload: dict, origin: str) -> dict:
        if not self.endpoint:
            raise AnalyticsRelayError()
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=data, method="POST", headers={
            "Content-Type": "application/json", "Origin": origin,
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(1025)
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read(1025)
        except (OSError, TimeoutError, ValueError) as exc:
            raise AnalyticsRelayError() from exc
        if len(raw) > 1024:
            raise AnalyticsRelayError()
        if status not in {200, 202}:
            raise AnalyticsRelayError(status if 400 <= status < 500 else 503)
        try:
            receipt = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnalyticsRelayError() from exc
        if not isinstance(receipt, dict) or receipt.get("accepted") is not True or not isinstance(receipt.get("duplicate"), bool):
            raise AnalyticsRelayError()
        return {"accepted": True, "duplicate": receipt["duplicate"]}
