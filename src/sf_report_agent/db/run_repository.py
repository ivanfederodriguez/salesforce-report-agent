from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


class ReportRunRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.migration_path = Path(__file__).with_name("migrations.sql")

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        migration = self.migration_path.read_text(encoding="utf-8")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(migration)

    def start_run(self, task_id: int) -> int:
        self.initialize()
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                "INSERT INTO report_runs(task_id, started_at, status) VALUES (?, ?, ?)",
                (task_id, _now(), "running"),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite no devolvió el ID de la corrida creada")
            return cursor.lastrowid

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        request: Any = None,
        plan: Any = None,
        permission_report: Any = None,
        soql: str | None = None,
        row_count: int | None = None,
        response_text: str | None = None,
        error: str | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE report_runs
                SET finished_at = ?, status = ?, request_json = ?, plan_json = ?,
                    permission_report_json = ?, soql = ?, row_count = ?, response_text = ?, error = ?
                WHERE id = ?
                """,
                (
                    _now(),
                    status,
                    _json(request),
                    _json(plan),
                    _json(permission_report),
                    soql,
                    row_count,
                    response_text,
                    error,
                    run_id,
                ),
            )

    def add_artifact(self, run_id: int, task_id: int, artifact_type: str, path: Path) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO report_artifacts(run_id, task_id, artifact_type, path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, task_id, artifact_type, str(path), _now()),
            )
