from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any


def _readable_api_name(value: str) -> str:
    cleaned = re.sub(r"__(?:c|r)$", "", value, flags=re.IGNORECASE)
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else value


class SalesforceLabelResolver:
    def __init__(self, schema_snapshot: dict[str, Any]) -> None:
        self.schema_snapshot = schema_snapshot

    def resolve(self, primary_object: str, fields: list[str]) -> dict[str, str]:
        labels = {
            field_name: self._resolve_field(primary_object, field_name)
            for field_name in fields
        }
        return self._disambiguate(labels)

    def resolve_value_labels(
        self, primary_object: str, fields: list[str]
    ) -> dict[str, dict[str, str]]:
        resolved: dict[str, dict[str, str]] = {}
        for field_name in fields:
            if "." in field_name:
                continue
            field = self._field(primary_object, field_name)
            values = field.get("picklistValues", []) if field else []
            labels = {
                str(item["value"]): str(item["label"])
                for item in values
                if isinstance(item, dict) and item.get("value") and item.get("label")
            }
            if labels:
                resolved[field_name] = labels
        return resolved

    def _object_fields(self, object_name: str) -> list[dict[str, Any]]:
        fields = (
            self.schema_snapshot.get("objects", {}).get(object_name, {}).get("fields", [])
        )
        return [field for field in fields if isinstance(field, dict)]

    def _field(self, object_name: str, field_name: str) -> dict[str, Any] | None:
        return next(
            (
                field
                for field in self._object_fields(object_name)
                if field.get("name") == field_name
            ),
            None,
        )

    @staticmethod
    def _label(field: dict[str, Any] | None, fallback: str) -> str:
        if field is not None:
            label = field.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
        return _readable_api_name(fallback)

    def _resolve_field(self, primary_object: str, field_name: str) -> str:
        if "." not in field_name:
            return self._label(self._field(primary_object, field_name), field_name)

        relationship_name, related_field_name = field_name.split(".", maxsplit=1)
        lookup = next(
            (
                field
                for field in self._object_fields(primary_object)
                if field.get("relationshipName") == relationship_name
                or self._inferred_relationship_name(str(field.get("name") or ""))
                == relationship_name
            ),
            None,
        )
        lookup_label = self._label(lookup, relationship_name)
        if related_field_name == "Name":
            return lookup_label
        references = lookup.get("referenceTo", []) if lookup else []
        related_object = (
            str(references[0])
            if isinstance(references, list) and references
            else ""
        )
        related_field = self._field(related_object, related_field_name) if related_object else None
        related_label = self._label(related_field, related_field_name)
        return f"{lookup_label}: {related_label}"

    @staticmethod
    def _inferred_relationship_name(field_name: str) -> str | None:
        if field_name.endswith("__c"):
            return field_name[:-3] + "__r"
        if field_name.endswith("Id"):
            return field_name[:-2]
        return None

    @staticmethod
    def _disambiguate(labels: dict[str, str]) -> dict[str, str]:
        counts = Counter(labels.values())
        seen: defaultdict[str, int] = defaultdict(int)
        resolved: dict[str, str] = {}
        for api_name, label in labels.items():
            if counts[label] == 1:
                resolved[api_name] = label
                continue
            seen[label] += 1
            context = (
                _readable_api_name(api_name.split(".", maxsplit=1)[0])
                if "." in api_name
                else _readable_api_name(api_name)
            )
            candidate = f"{label} ({context})"
            if candidate in resolved.values():
                candidate = f"{candidate} {seen[label]}"
            resolved[api_name] = candidate
        return resolved
