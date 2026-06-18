from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sf_report_agent.models.task import ExternalTask

TASK_COLUMNS = {
    "id",
    "created_at",
    "channel_id",
    "message_ts",
    "user_id",
    "sender_label",
    "conversation_label",
    "summary",
    "requested_action",
    "priority",
    "category",
    "status",
    "classification_json",
    "public_request_text",
    "thread_ts",
    "requester_label",
    "updated_at",
}
LINK_COLUMNS = {
    "channel_id",
    "message_ts",
    "url",
    "url_type",
    "title",
    "metadata_json",
}


class SourceDatabaseError(RuntimeError):
    """Error legible en el contrato con la base fuente."""


class TaskReader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise SourceDatabaseError(f"No existe la SQLite fuente: {self.db_path}")
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        try:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.DatabaseError as exc:
            raise SourceDatabaseError(f"No se pudo inspeccionar la tabla {table}: {exc}") from exc
        if not rows:
            raise SourceDatabaseError(f"La SQLite fuente no contiene la tabla requerida '{table}'")
        return {str(row["name"]) for row in rows}

    def validate_schema(self, connection: sqlite3.Connection) -> None:
        task_missing = TASK_COLUMNS - self._columns(connection, "tasks")
        link_missing = LINK_COLUMNS - self._columns(connection, "message_links")
        errors: list[str] = []
        if task_missing:
            errors.append(f"tasks: faltan {', '.join(sorted(task_missing))}")
        if link_missing:
            errors.append(f"message_links: faltan {', '.join(sorted(link_missing))}")
        if errors:
            raise SourceDatabaseError("Schema incompatible en SQLite fuente; " + "; ".join(errors))

    def list_tasks(self, *, limit: int = 20, salesforce_only: bool = False) -> list[ExternalTask]:
        if limit < 1:
            raise ValueError("limit debe ser mayor que cero")
        with self._connect() as connection:
            self.validate_schema(connection)
            where = "WHERE lower(category) = 'salesforce'" if salesforce_only else ""
            rows = connection.execute(
                f"SELECT * FROM tasks {where} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_task(connection, row) for row in rows]

    def get_task(self, task_id: int) -> ExternalTask:
        with self._connect() as connection:
            self.validate_schema(connection)
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise SourceDatabaseError(f"No existe la tarea id={task_id} en {self.db_path}")
            return self._row_to_task(connection, row)

    def next_salesforce_task(self) -> ExternalTask | None:
        with self._connect() as connection:
            self.validate_schema(connection)
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE lower(category) = 'salesforce'
                  AND coalesce(status, 'new') NOT IN ('done', 'done_pending_reply', 'cancelled')
                ORDER BY id ASC LIMIT 1
                """
            ).fetchone()
            return self._row_to_task(connection, row) if row is not None else None

    def list_pending_salesforce_tasks(self, statuses: Sequence[str]) -> list[ExternalTask]:
        normalized_statuses = tuple(
            dict.fromkeys(status.strip().casefold() for status in statuses if status.strip())
        )
        if not normalized_statuses:
            raise ValueError("Debe indicarse al menos un status de tarea fuente")
        placeholders = ", ".join("?" for _ in normalized_statuses)
        with self._connect() as connection:
            self.validate_schema(connection)
            rows = connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE lower(trim(category)) = 'salesforce'
                  AND lower(trim(coalesce(status, 'new'))) IN ({placeholders})
                ORDER BY id ASC
                """,
                normalized_statuses,
            ).fetchall()
            return [self._row_to_task(connection, row) for row in rows]

    def _row_to_task(self, connection: sqlite3.Connection, row: sqlite3.Row) -> ExternalTask:
        raw_classification = row["classification_json"] or "{}"
        try:
            classification = json.loads(raw_classification)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SourceDatabaseError(
                f"classification_json inválido para task id={row['id']}: {exc}"
            ) from exc
        if not isinstance(classification, dict):
            raise SourceDatabaseError(
                f"classification_json de task id={row['id']} debe ser un objeto JSON"
            )
        links = connection.execute(
            """
            SELECT channel_id, message_ts, url, url_type, title, metadata_json
            FROM message_links WHERE channel_id = ? AND message_ts = ? ORDER BY id
            """,
            (row["channel_id"], row["message_ts"]),
        ).fetchall()
        parsed_links: list[dict[str, Any]] = []
        for link in links:
            item = dict(link)
            metadata = item.get("metadata_json")
            if metadata:
                try:
                    item["metadata_json"] = json.loads(metadata)
                except json.JSONDecodeError:
                    item["metadata_json"] = {"raw": metadata}
            parsed_links.append(item)
        return ExternalTask(
            id=row["id"],
            created_at=row["created_at"],
            sender_label=row["sender_label"],
            conversation_label=row["conversation_label"],
            requested_action=row["requested_action"],
            public_request_text=row["public_request_text"],
            category=row["category"],
            priority=row["priority"],
            status=row["status"],
            classification_json=classification,
            message_links=parsed_links,
        )
