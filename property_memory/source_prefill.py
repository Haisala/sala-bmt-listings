"""Deterministic, evidence-linked candidates extracted from a saved Intake note.

This module never confirms facts or rights. Returned values are private
candidate facts that still require the existing Owner review gates.
"""
from __future__ import annotations

import re
import hashlib
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any


def _fold_with_offsets(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        value = unicodedata.normalize("NFKD", char.casefold().replace("đ", "d"))
        for part in value:
            if not unicodedata.combining(part):
                chars.append(part)
                offsets.append(index)
    return "".join(chars), offsets


def _source_span(text: str, offsets: list[int], start: int, end: int) -> tuple[str, int, int]:
    while start < end and text[offsets[start]].isspace():
        start += 1
    while end > start and text[offsets[end - 1]].isspace():
        end -= 1
    if start >= end:
        return "", 0, 0
    begin, finish = offsets[start], offsets[end - 1] + 1
    return text[begin:finish].strip(), begin, finish


def _fact(value: Any, text: str, offsets: list[int], span: tuple[int, int], intake_id: str,
          source_revision: int, source_note: str) -> tuple[Any, dict[str, Any]]:
    excerpt, start, end = _source_span(text, offsets, *span)
    return value, {
        "intake_id": intake_id,
        "source_id": f"intake:{intake_id}:source:r{source_revision}",
        "source_revision": source_revision,
        "origin": "LOCAL_SOURCE_EXTRACTED",
        "confidence": "CANDIDATE",
        "evidence_excerpt": excerpt,
        "evidence_start": start,
        "evidence_end": end,
        "source_note_sha256": hashlib.sha256(source_note.encode("utf-8")).hexdigest(),
    }


def extract_source_facts(note: str, *, intake_id: str = "", source_revision: int = 1) -> dict[str, Any]:
    """Extract conservative candidate facts plus evidence spans/conflicts."""
    text = str(note or "")
    folded, offsets = _fold_with_offsets(text)
    facts: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    def put(field: str, value: Any, span: tuple[int, int]) -> None:
        facts[field], provenance[field] = _fact(value, text, offsets, span, intake_id, source_revision, text)

    transaction_matches = list(re.finditer(r"\b(cho thue|thue|rent|rental|ban|sale)\b", folded))
    transaction_values = {"RENT" if x.group(1) in {"cho thue", "thue", "rent", "rental"} else "SALE" for x in transaction_matches}
    if len(transaction_values) == 1 and transaction_matches:
        match = transaction_matches[0]
        put("transaction_type", next(iter(transaction_values)), match.span())
    elif len(transaction_values) > 1:
        conflicts.append({"field": "transaction_type", "kind": "SOURCE_CONFLICT", "candidates": sorted(transaction_values), "evidence": [_source_span(text, offsets, *m.span())[0] for m in transaction_matches]})

    type_patterns = (("APARTMENT", r"\b(can ho|apartment)\b"),
                     ("HOUSE", r"\b(biet thu|nha pho|nha rieng|nha cap)\b"),
                     ("LAND", r"\b(dat nen|lo dat|ban dat|nen dat)\b"))
    type_hits = [(kind, m) for kind, pattern in type_patterns for m in re.finditer(pattern, folded)]
    type_values = {kind for kind, _ in type_hits}
    if len(type_values) == 1 and type_hits:
        kind, match = type_hits[0]
        put("property_type", kind, match.span())
        type_phrase = match.group(1)
        if kind == "HOUSE" and type_phrase == "biet thu":
            put("house_type", "BIET_THU", match.span())
        elif kind == "HOUSE" and type_phrase == "nha pho":
            put("house_type", "NHA_PHO", match.span())
    elif len(type_values) > 1:
        conflicts.append({"field": "property_type", "kind": "SOURCE_CONFLICT", "candidates": sorted(type_values), "evidence": [_source_span(text, offsets, *m.span())[0] for _, m in type_hits]})

    # Explicit labels are preferred. A type-led source phrase is a candidate
    # title only and remains subject to the existing Owner confirmation gate.
    title_match = re.search(r"\b(?:ten\s+(?:bds|bat dong san|nha|can)|tieu de)\s*[:=-]\s*([^\n;.!]{3,100})", folded)
    if title_match:
        group = title_match.span(1)
        title, _, _ = _source_span(text, offsets, *group)
        if title:
            put("title", title, group)
    else:
        title_match = re.search(r"\b(biet thu|nha pho|nha rieng|can ho|dat nen)\b([^,;.!\n]{0,70}?)(?=\s+(?:tai|o|khu vuc|dia chi|vi tri)\b|[,;.!\n]|$)", folded)
        if title_match:
            candidate_title, _, _ = _source_span(text, offsets, *title_match.span())
            if candidate_title:
                put("title", candidate_title, title_match.span())

    # A location can be a named development/area; this is not treated as an
    # exact street address. Capture only source phrases with explicit context.
    location_match = re.search(r"\b(?:dia chi|vi tri|khu vuc|toa lac|tai|o)\s*[:=-]?\s*([^\n;.!]{2,100})", folded)
    if not location_match:
        location_match = re.search(r"\bmat tien\s+([^,;.!\n]{2,80})", folded)
    if location_match:
        group = location_match.span(1)
        loc, _, _ = _source_span(text, offsets, *group)
        loc = re.split(r"\s+(?:gia|dien tich|rong|dai|xay dung)\b", loc, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,.-")
        if loc:
            put("address", loc, group)

    area_matches = list(re.finditer(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:m\s*2|sqm|met vuong)\b", folded))
    area_candidates: dict[str, list[tuple[Decimal, re.Match[str]]]] = {"area": [], "land_area": [], "construction_area": []}
    for match in area_matches:
        try:
            value = Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            continue
        context = folded[max(0, match.start() - 36):match.start()]
        if re.search(r"(xay dung|san xay|dien tich xay|construction)\s*$", context):
            field = "construction_area"
        elif re.search(r"(dat|khuon vien|dien tich dat|tho cu)\s*$", context):
            field = "land_area"
        else:
            field = "area"
        area_candidates[field].append((value, match))
    for field, candidates in area_candidates.items():
        unique = {value for value, _ in candidates}
        if len(unique) == 1:
            value, match = candidates[0]
            display = f"{format(value.normalize(), 'f')} m²"
            if field in {"land_area", "construction_area"}:
                put(field, float(value), match.span())
                if field == "land_area" and "area" not in facts:
                    put("area", display, match.span())
            else:
                put("area", display, match.span())
        elif len(unique) > 1:
            conflicts.append({"field": field, "kind": "SOURCE_CONFLICT", "candidates": [float(x) for x in sorted(unique)], "evidence": [_source_span(text, offsets, *m.span())[0] for _, m in candidates]})

    dimensions = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?!\d)", folded)
    if dimensions:
        for field, group in (("width", 1), ("length", 2)):
            try:
                put(field, float(Decimal(dimensions.group(group).replace(",", "."))), dimensions.span(group))
            except InvalidOperation:
                pass

    amounts = list(re.finditer(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(ty|ti|trieu|tr|nghin|ngan)\b", folded))
    compound_amount = None
    if len(amounts) == 2 and amounts[0].group(2) in {"ty", "ti"} and amounts[1].group(2) in {"trieu", "tr"}:
        gap = folded[amounts[0].end():amounts[1].start()]
        if len(gap) <= 12:
            try:
                compound_amount = (int(Decimal(amounts[0].group(1).replace(",", ".")) * Decimal(1_000_000_000)
                                      + Decimal(amounts[1].group(1).replace(",", ".")) * Decimal(1_000_000)),
                                   (amounts[0].start(), amounts[1].end()))
            except InvalidOperation:
                compound_amount = None
    sale = "SALE" in transaction_values
    rent = "RENT" in transaction_values
    monthly = bool(re.search(r"/\s*thang|moi\s*thang|hang\s*thang|per\s*month", folded))
    if len(amounts) == 1 or compound_amount:
        match = amounts[0]
        try:
            if compound_amount:
                amount, amount_span = compound_amount
            else:
                value = Decimal(match.group(1).replace(",", "."))
                multiplier = Decimal(1_000_000_000 if match.group(2) in {"ty", "ti"} else 1_000_000 if match.group(2) in {"trieu", "tr"} else 1_000)
                amount, amount_span = int(value * multiplier), match.span()
            if rent or monthly:
                put("transaction_type", "RENT", amount_span) if not transaction_values else None
                put("price_vnd_per_month", amount, amount_span)
                put("price_cycle", "MONTH", amount_span)
            elif sale:
                put("price_vnd", amount, amount_span)
        except InvalidOperation:
            pass
    elif len(amounts) > 1:
        conflicts.append({"field": "price_vnd_per_month" if rent or monthly else "price_vnd", "kind": "SOURCE_CONFLICT", "candidates": [m.group(0) for m in amounts], "evidence": [_source_span(text, offsets, *m.span())[0] for m in amounts]})

    for field, pattern in (("bedrooms", r"(\d+)\s*(?:phong ngu|bedrooms?)"), ("bathrooms", r"(\d+)\s*(?:wc|phong tam|bathrooms?)")):
        match = re.search(pattern, folded)
        if match:
            put(field, int(match.group(1)), match.span())

    return {"facts": facts, "provenance": provenance, "conflicts": conflicts,
            "source_id": f"intake:{intake_id}:source:r{source_revision}"}
