"""Compatibility import for the single shared public DTO contract."""
from customer_web_v2.public_contract import (
    ALLOWED_FIELDS,
    PRIVATE_KEYS,
    REQUIRED,
    STATUSES,
    PublicContractError,
    to_legacy_web_record,
    validate_public_dto,
)

__all__ = [
    "ALLOWED_FIELDS", "PRIVATE_KEYS", "REQUIRED", "STATUSES", "PublicContractError",
    "to_legacy_web_record", "validate_public_dto",
]
