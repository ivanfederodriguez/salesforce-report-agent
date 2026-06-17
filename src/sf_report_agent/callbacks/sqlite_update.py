from __future__ import annotations

import sqlite3
from pathlib import Path

from sf_report_agent.db.task_reader import SourceDatabaseError


def mark_source_task_done_pending_reply(db_path: Path, task_id: int) -> None:
    """Única mutación opcional de la fuente; jamás envía mensajes a Slack."""
    if not db_path.exists():
        raise SourceDatabaseError(f"No existe la SQLite fuente: {db_path}")
    with sqlite3.connect(db_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if "status" not in columns:
            raise SourceDatabaseError("No se puede actualizar la fuente: tasks.status no existe")
        cursor = connection.execute(
            "UPDATE tasks SET status = 'done_pending_reply' WHERE id = ?", (task_id,)
        )
        if cursor.rowcount != 1:
            raise SourceDatabaseError(f"No se encontró task id={task_id} para actualizar")
