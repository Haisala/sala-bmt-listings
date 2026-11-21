"""Private Slice 1 orchestration: Intake -> Candidate -> Matching -> Review."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .intake_adapter import to_candidate
from .intelligence import enrich_candidate
from .matcher import evaluate_candidate
from .review_center import ReviewCenter


def process_intake(store: Any, review_center: ReviewCenter, intake: dict[str, Any], *, ai_gateway: Any = None, operation_id: str | None = None) -> dict[str, Any]:
    """Create one persistent pending review; never mutates canonical property data."""
    candidate = to_candidate(intake)
    ai_succeeded = False
    if ai_gateway is not None:
        ai_result = ai_gateway.analyze(intake, operation_id=operation_id or candidate["intake_id"])
        if ai_result.status == "SUCCEEDED" and ai_result.result is not None:
            candidate = enrich_candidate(candidate, ai_result.result)
            ai_succeeded = True
    existing = review_center._existing_for_candidate(candidate["candidate_id"])
    if existing:
        if ai_succeeded:
            matches = evaluate_candidate(store, candidate, persist=True)
            return review_center.refresh_intake_package(existing["review_id"], candidate, matches)
        return deepcopy(existing)
    matches = evaluate_candidate(store, candidate, persist=True)
    return review_center.create_intake_package(deepcopy(candidate), matches)
