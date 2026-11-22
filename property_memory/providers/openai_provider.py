"""Production-shaped OpenAI Responses adapter; never called by tests by default."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
import ssl
import re
from datetime import datetime, timezone
from typing import Any, Protocol
from ..intelligence import extract as validate_extract
from ..keychain import CredentialError, resolve_openai_key

DEFAULT_EXTRACTION_MODEL = "gpt-6-luna"
PROPERTY_EXTRACTION_PROMPT_VERSION = "bmt-property-v1"
ENDPOINT = "https://api.openai.com/v1/responses"
CONTENT_PROMPT_VERSION = "content-studio-v2"

EXTRACTION_RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "fields": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
            "field": {"type": "string"},
            "raw_value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]},
            "normalized_value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]},
            "origin": {"type": "string", "enum": ["AI_EXTRACTED", "AI_INFERRED"]},
            "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "provenance": {"type": "object", "additionalProperties": False, "properties": {"intake_id": {"type": "string"}}, "required": ["intake_id"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
        }, "required": ["field", "raw_value", "normalized_value", "origin", "confidence", "evidence", "provenance", "warnings"]}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"field": {"type": "string"}, "message": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["field", "message", "evidence"]}},
        "rights_status": {"type": "string", "enum": ["PERSONAL", "COMPANY", "UNKNOWN"]},
        "raw_source": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    "required": ["fields", "missing_fields", "conflicts", "rights_status", "raw_source"],
}

CONTENT_RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"variants": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "angle": {"type": "string", "enum": ["NEEDS_USE", "ASSET_VALUE", "STORYTELLING"]},
            "body": {"type": "string"}, "title": {"type": "string"},
            "facts_used": {"type": "array", "items": {"type": "string"}},
            "claims": {"type": "array", "items": {"type": "string"}},
            "unknowns": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["angle", "body", "title", "facts_used", "claims", "unknowns", "warnings"],
    }}},
    "required": ["variants"],
}

MAX_CONTENT_REQUEST_BYTES = 8000
MAX_CONTENT_INPUT_TOKENS = 1200
MAX_CONTENT_OUTPUT_TOKENS = 900
MAX_CONTENT_COST_USD = 0.01
CONTENT_FACT_PRIORITY = (
    "title", "property_type", "address", "area", "price_vnd", "price_vnd_per_month",
    "price", "price_type", "legal_status", "house_type", "bedrooms", "bathrooms",
    "road_access", "features", "description",
)

class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, diagnostics: dict[str, Any] | None = None):
        super().__init__(message[:240]); self.code = code; self.retryable = retryable
        self.diagnostics = diagnostics or {}

class Transport(Protocol):
    def send(self, payload: dict[str, Any], api_key: str, *, timeout: float) -> dict[str, Any]: ...

class UrllibTransport:
    def send(self, payload: dict[str, Any], api_key: str, *, timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, method="POST")
        try:
            context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            details: dict[str, Any] = {"http_status": int(status), "endpoint": ENDPOINT}
            try:
                payload = json.loads(exc.read(4096).decode("utf-8", "replace"))
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    for source, target in (("type", "error_type"), ("code", "error_code"), ("param", "error_param"), ("message", "error_message")):
                        value = error.get(source)
                        if isinstance(value, (str, int)):
                            safe = re.sub(r"(?i)(bearer\s+|sk-[a-z0-9_-]+|api[_-]?key|token|cookie|authorization)[^\s,;]*", "[REDACTED]", str(value))
                            details[target] = safe[:240 if target == "error_message" else 96]
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
            raise ProviderError("RATE_LIMIT" if status == 429 else ("PROVIDER_5XX" if status >= 500 else "PROVIDER_4XX"), "OpenAI provider HTTP failure", retryable=status == 429 or status >= 500, diagnostics=details) from None
        except TimeoutError:
            raise ProviderError("PROVIDER_TIMEOUT", "OpenAI provider timeout", retryable=True) from None
        except (urllib.error.URLError, json.JSONDecodeError):
            raise ProviderError("PROVIDER_TRANSPORT", "OpenAI provider transport failure", retryable=True) from None

def extraction_instructions() -> str:
    return """Extract only facts explicitly supported by the Vietnamese real-estate source. Return JSON fields with field, raw_value, normalized_value, origin (AI_EXTRACTED or AI_INFERRED), confidence (HIGH/MEDIUM/LOW), evidence, provenance. Derived values must be AI_INFERRED. Never emit OWNER_CONFIRMED or publishing rights. Preserve missing_fields and conflicts. Use integer VND only when unambiguous."""

def build_request(intake: dict[str, Any], model: str) -> dict[str, Any]:
    allowed = {"id": intake.get("id") or intake.get("intake_id"), "source": intake.get("source"), "sourceGroup": intake.get("sourceGroup"), "text": intake.get("text") or intake.get("note"), "preliminaryFacts": intake.get("preliminaryFacts") or intake.get("preliminary") or intake.get("facts") or {}, "media": [{k: m.get(k) for k in ("path", "sha256", "role") if m.get(k) is not None} for m in intake.get("media", []) if isinstance(m, dict)]}
    return {"model": model, "instructions": extraction_instructions(), "input": "Return JSON only.\n" + json.dumps(allowed, ensure_ascii=False), "text": {"format": {"type": "json_schema", "name": "property_extraction", "strict": True, "schema": EXTRACTION_RESPONSE_SCHEMA}}}


def _content_cost_upper_bound(request_bytes: int, max_output_tokens: int) -> float:
    # Request UTF-8 bytes conservatively bound text input tokens. Rates are doubled
    # from current standard gpt-6-luna pricing to leave room for price/tier variance.
    return (request_bytes * 0.20 + max_output_tokens * 1.00) / 1_000_000


def build_content_request(payload: dict[str, Any], model: str, *, max_output_tokens: int = MAX_CONTENT_OUTPUT_TOKENS) -> dict[str, Any]:
    """Build one bounded three-variant request from revision-bound approved facts."""
    if model != DEFAULT_EXTRACTION_MODEL or max_output_tokens != MAX_CONTENT_OUTPUT_TOKENS:
        raise ValueError("CONTENT_MODEL_OR_OUTPUT_CAP_INVALID")
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    instructions = ("Write 3 distinct Vietnamese property drafts, one per angle. Use only listed facts. "
                    "Do not invent legal status, ownership, amenities, returns, or media rights. List claims, unknowns, warnings. JSON only.")
    schema_bytes = len(json.dumps(CONTENT_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def compose(selected_facts: dict[str, Any]) -> dict[str, Any]:
        # Lineage identifiers remain attached to the saved batch/receipt; the
        # model needs only approved facts, so do not spend prompt tokens on IDs.
        allowed = {"facts": selected_facts, "angles": ["NEEDS_USE", "ASSET_VALUE", "STORYTELLING"],
                   "output_count": 3}
        request = {"model": model, "instructions": instructions,
                   "input": json.dumps(allowed, ensure_ascii=False, separators=(",", ":")),
                   "max_output_tokens": max_output_tokens,
                   "text": {"format": {"type": "json_schema", "name": "content_studio_three", "strict": True,
                                        "schema": CONTENT_RESPONSE_SCHEMA}}}
        request_bytes = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        input_token_upper_bound = (len(instructions.encode("utf-8")) + len(request["input"].encode("utf-8"))
                                   + schema_bytes + len("content_studio_three"))
        return request if request_bytes <= MAX_CONTENT_REQUEST_BYTES and input_token_upper_bound <= MAX_CONTENT_INPUT_TOKENS else None

    selected: dict[str, Any] = {}
    request = compose(selected)
    for field in CONTENT_FACT_PRIORITY:
        value = facts.get(field)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            value = value[:160]
        elif field == "features" and isinstance(value, list):
            value = [str(item)[:80] for item in value[:5] if str(item).strip()]
        elif not isinstance(value, (int, float, bool)):
            continue
        candidate = {**selected, field: value}
        bounded = compose(candidate)
        if bounded is not None:
            selected = candidate
            request = bounded
    if request is None or not selected:
        raise ValueError("CONTENT_INPUT_LIMIT_EXCEEDED")
    request_bytes = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if _content_cost_upper_bound(request_bytes, max_output_tokens) >= MAX_CONTENT_COST_USD:
        raise ValueError("CONTENT_COST_CAP_EXCEEDED")
    return request

def _response_json(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response.get("output_json"), dict): return response["output_json"]
    text = response.get("output_text")
    if isinstance(text, str):
        try: return json.loads(text)
        except json.JSONDecodeError: pass
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict): continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    try: return json.loads(content["text"])
                    except json.JSONDecodeError: continue
    raise ProviderError("MALFORMED_STRUCTURED_OUTPUT", "Provider structured output was invalid")


def _safe_response_diagnostics(response: dict[str, Any]) -> dict[str, Any]:
    """Keep billing/debug metadata on malformed responses without retaining content."""
    diagnostics: dict[str, Any] = {}
    request_id = response.get("id")
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request_id):
        diagnostics["provider_request_id"] = request_id
    status = response.get("status")
    if isinstance(status, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", status):
        diagnostics["response_status"] = status
    incomplete = response.get("incomplete_details")
    reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
    if isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", reason):
        diagnostics["incomplete_reason"] = reason
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", code):
            diagnostics["response_error_code"] = code
    usage = response.get("usage")
    if isinstance(usage, dict):
        safe_usage: dict[str, Any] = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_usage[key] = value
        for section, keys in (("input_tokens_details", ("cached_tokens", "cache_write_tokens")),
                              ("output_tokens_details", ("reasoning_tokens",))):
            values = usage.get(section)
            if isinstance(values, dict):
                safe_values = {key: values[key] for key in keys
                               if isinstance(values.get(key), int) and not isinstance(values.get(key), bool) and values[key] >= 0}
                if safe_values:
                    safe_usage[section] = safe_values
        if safe_usage:
            diagnostics["usage"] = safe_usage
    shapes = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output[:12]:
            if not isinstance(item, dict):
                continue
            typ = item.get("type")
            if isinstance(typ, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", typ):
                shapes.append(typ)
            content = item.get("content")
            if isinstance(content, list):
                for part in content[:12]:
                    if isinstance(part, dict):
                        part_type = part.get("type")
                        if isinstance(part_type, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", part_type):
                            shapes.append(part_type)
    if shapes:
        diagnostics["output_item_types"] = shapes[:24]
    return diagnostics

class OpenAIPropertyProvider:
    provider_name = "openai-responses"
    def __init__(self, *, api_key: str | None = None, model: str | None = None, transport: Transport | None = None, timeout: float = 30.0, usage_sink: Any | None = None):
        self.api_key = api_key if api_key is not None else None
        self._credential_resolver = resolve_openai_key
        self.model = model or os.environ.get("BMT_OPENAI_MODEL", DEFAULT_EXTRACTION_MODEL)
        self.transport = transport or UrllibTransport(); self.timeout = timeout; self.usage_sink = usage_sink
    def extract(self, source: dict[str, Any]) -> dict[str, Any]:
        key = self.api_key
        if key is None:
            try: key = self._credential_resolver()
            except CredentialError as exc: raise ProviderError(exc.code, "OpenAI credential is not configured") from None
            except Exception: raise ProviderError("MISSING_CREDENTIAL", "OpenAI credential is not configured") from None
        payload = build_request(source, self.model)
        try:
            response = self.transport.send(payload, key, timeout=self.timeout)
        except ProviderError as exc:
            exc.diagnostics.setdefault("model", self.model)
            exc.diagnostics.setdefault("project_header_configured", bool(os.environ.get("OPENAI_PROJECT")))
            raise
        try:
            value = _response_json(response)
        except ProviderError as exc:
            exc.diagnostics.update(_safe_response_diagnostics(response))
            exc.diagnostics.setdefault("model", self.model)
            raise
        usage = response.get("usage", {}) if isinstance(response.get("usage", {}), dict) else {}
        value["metadata"] = {"provider": self.provider_name, "model": self.model, "request_id": response.get("id") if isinstance(response.get("id"), str) else None, "prompt_version": PROPERTY_EXTRACTION_PROMPT_VERSION, "processed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "usage": usage}
        if self.usage_sink is not None:
            receipt_id = "receipt-" + __import__("hashlib").sha256((str(source.get("id") or source.get("intake_id")) + ":" + self.model).encode()).hexdigest()[:24]
            self.usage_sink.record(receipt_id=receipt_id, intake_id=str(source.get("id") or source.get("intake_id")), stage="intake_extraction", operation="provider.extract", provider=self.provider_name, model=self.model, outcome="SUCCEEDED", usage=usage, provider_request_id=response.get("id") if isinstance(response.get("id"), str) else None, idempotency_key=receipt_id)
        return value
    def extract_validated(self, source: dict[str, Any]):
        return validate_extract(self, source)

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate Content through the same existing credential/Responses transport."""
        key = self.api_key
        if key is None:
            try: key = self._credential_resolver()
            except CredentialError as exc: raise ProviderError(exc.code, "OpenAI credential is not configured") from None
            except Exception: raise ProviderError("MISSING_CREDENTIAL", "OpenAI credential is not configured") from None
        request_payload = build_content_request(payload, self.model)
        try:
            response = self.transport.send(request_payload, key, timeout=self.timeout)
        except ProviderError as exc:
            exc.diagnostics.setdefault("model", self.model)
            raise
        try:
            value = _response_json(response)
        except ProviderError as exc:
            exc.diagnostics.update(_safe_response_diagnostics(response))
            exc.diagnostics.setdefault("model", self.model)
            raise
        if not isinstance(value.get("variants"), list) or len(value["variants"]) != 3:
            raise ProviderError("MALFORMED_CONTENT_OUTPUT", "Provider content output was not exactly three variants")
        value["usage"] = response.get("usage") if isinstance(response.get("usage"), dict) else None
        value["provider_request_id"] = response.get("id") if isinstance(response.get("id"), str) else None
        value["metadata"] = {"provider": self.provider_name, "model": self.model, "prompt_version": CONTENT_PROMPT_VERSION}
        return value
