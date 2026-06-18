from __future__ import annotations


def normalize_salesforce_id(value: str) -> str:
    """Return the case-insensitive 15-character key for a Salesforce ID."""
    return value.strip()[:15].upper()
