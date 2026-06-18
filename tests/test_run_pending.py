from __future__ import annotations

import sqlite3
from pathlib import Path

from conftest import create_source_database
from sf_report_agent.config import Settings
from sf_report_agent.db.run_repository import ReportRunRepository
from sf_report_agent.main import build_parser, run_pending_tasks
from sf_report_agent.models.execution_result import ExecutionResult
from sf_report_agent.models.task import ExternalTask


def _settings(tmp_path: Path, source_db: Path) -> Settings:
    return Settings(
        source_db_path=source_db,
        worker_db_path=tmp_path / "worker.db",
        artifacts_dir=tmp_path / "artifacts",
        field_mapping_path=None,
        model_provider="ollama",
        ollama_model="test",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_temperature=0,
        salesforce_username=None,
        salesforce_password=None,
        salesforce_security_token=None,
        salesforce_domain="login",
        sf_read_only=True,
        max_export_rows=100,
        require_human_approval_for_pii=True,
        log_pii=False,
        update_source_task=False,
    )


def _source_database(
    path: Path,
    sample: ExternalTask,
    rows: list[tuple[int, str, str]],
) -> None:
    first_id, first_status, first_category = rows[0]
    create_source_database(
        path,
        sample.model_copy(
            update={"id": first_id, "status": first_status, "category": first_category}
        ),
    )
    with sqlite3.connect(path) as connection:
        for task_id, status, category in rows[1:]:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, created_at, channel_id, message_ts, user_id, sender_label,
                    conversation_label, summary, requested_action, priority, category,
                    status, classification_json, public_request_text, thread_ts,
                    requester_label, updated_at
                )
                SELECT ?, created_at, channel_id, message_ts, user_id, sender_label,
                       conversation_label, summary, requested_action, priority, ?,
                       ?, classification_json, public_request_text, thread_ts,
                       requester_label, updated_at
                FROM tasks WHERE id = ?
                """,
                (task_id, category, status, first_id),
            )


class PersistingRunner:
    def __init__(
        self,
        repository: ReportRunRepository,
        *,
        failures: set[int] | None = None,
        statuses: dict[int, str] | None = None,
    ) -> None:
        self.repository = repository
        self.failures = failures or set()
        self.statuses = statuses or {}
        self.calls: list[tuple[int, bool]] = []

    def run(self, task_id: int, *, dry_run: bool = False) -> ExecutionResult:
        self.calls.append((task_id, dry_run))
        run_id = self.repository.start_run(task_id)
        if task_id in self.failures:
            self.repository.finish_run(run_id, status="failed", error="boom")
            raise RuntimeError("boom")
        status = (
            "dry_run_completed" if dry_run else self.statuses.get(task_id, "done_pending_approval")
        )
        self.repository.finish_run(run_id, status=status)
        return ExecutionResult(task_id=task_id, status=status)


def _finish(repository: ReportRunRepository, task_id: int, status: str) -> None:
    run_id = repository.start_run(task_id)
    repository.finish_run(run_id, status=status)


def test_run_pending_processes_multiple_salesforce_tasks(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(
        source_db,
        micaela_task,
        [(1, "new", "salesforce"), (2, "new", "salesforce"), (3, "new", "other")],
    )
    settings = _settings(tmp_path, source_db)
    runner = PersistingRunner(
        ReportRunRepository(settings.worker_db_path),
        statuses={2: "needs_clarification"},
    )

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=False,
        runner=runner,
    )

    assert runner.calls == [(1, False), (2, False)]
    assert summary.processed == 2
    assert summary.pending_approval == 1
    assert summary.needs_clarification == 1


def test_run_pending_does_not_reprocess_finalized_tasks(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(source_db, micaela_task, [(1, "new", "salesforce"), (2, "new", "salesforce")])
    settings = _settings(tmp_path, source_db)
    repository = ReportRunRepository(settings.worker_db_path)
    _finish(repository, 1, "done_pending_reply")
    runner = PersistingRunner(repository)

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=False,
        runner=runner,
    )

    assert summary.skipped_task_ids == [1]
    assert runner.calls == [(2, False)]


def test_run_pending_continues_and_persists_failure(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(
        source_db,
        micaela_task,
        [(1, "new", "salesforce"), (2, "new", "salesforce"), (3, "new", "salesforce")],
    )
    settings = _settings(tmp_path, source_db)
    repository = ReportRunRepository(settings.worker_db_path)
    runner = PersistingRunner(repository, failures={2})

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=False,
        runner=runner,
    )

    assert runner.calls == [(1, False), (2, False), (3, False)]
    assert summary.processed == 2
    assert summary.failed_task_ids == [2]
    with sqlite3.connect(settings.worker_db_path) as connection:
        failed = connection.execute(
            "SELECT status, error FROM report_runs WHERE task_id = 2"
        ).fetchone()
    assert failed == ("failed", "boom")


def test_run_pending_stop_on_error_stops_before_next_task(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(
        source_db,
        micaela_task,
        [(1, "new", "salesforce"), (2, "new", "salesforce"), (3, "new", "salesforce")],
    )
    settings = _settings(tmp_path, source_db)
    runner = PersistingRunner(ReportRunRepository(settings.worker_db_path), failures={2})

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=True,
        runner=runner,
    )

    assert runner.calls == [(1, False), (2, False)]
    assert summary.processed_task_ids == [1]
    assert summary.failed_task_ids == [2]


def test_run_pending_respects_limit_after_skipped_tasks(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(
        source_db,
        micaela_task,
        [(1, "new", "salesforce"), (2, "new", "salesforce"), (3, "new", "salesforce")],
    )
    settings = _settings(tmp_path, source_db)
    repository = ReportRunRepository(settings.worker_db_path)
    _finish(repository, 1, "dry_run_completed")
    runner = PersistingRunner(repository)

    summary = run_pending_tasks(
        settings,
        limit=2,
        dry_run=False,
        stop_on_error=False,
        runner=runner,
    )

    assert summary.skipped_task_ids == [1]
    assert runner.calls == [(2, False), (3, False)]


def test_run_pending_respects_dry_run(tmp_path: Path, micaela_task: ExternalTask) -> None:
    source_db = tmp_path / "source.db"
    _source_database(source_db, micaela_task, [(1, "new", "salesforce"), (2, "new", "salesforce")])
    settings = _settings(tmp_path, source_db)
    repository = ReportRunRepository(settings.worker_db_path)
    runner = PersistingRunner(repository)

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=True,
        stop_on_error=False,
        runner=runner,
    )

    assert runner.calls == [(1, True), (2, True)]
    assert summary.task_statuses == {1: "dry_run_completed", 2: "dry_run_completed"}
    assert repository.has_processed_run(1)


def test_run_pending_force_reprocesses_finalized_task(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(source_db, micaela_task, [(1, "new", "salesforce")])
    settings = _settings(tmp_path, source_db)
    repository = ReportRunRepository(settings.worker_db_path)
    _finish(repository, 1, "needs_clarification")
    runner = PersistingRunner(repository)

    summary = run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=False,
        force=True,
        runner=runner,
    )

    assert summary.skipped == 0
    assert runner.calls == [(1, False)]


def test_run_pending_includes_requested_source_status_and_worker_is_alias(
    tmp_path: Path, micaela_task: ExternalTask
) -> None:
    source_db = tmp_path / "source.db"
    _source_database(
        source_db,
        micaela_task,
        [(1, "new", "salesforce"), (2, "needs_retry", "salesforce")],
    )
    settings = _settings(tmp_path, source_db)
    runner = PersistingRunner(ReportRunRepository(settings.worker_db_path))

    run_pending_tasks(
        settings,
        limit=10,
        dry_run=False,
        stop_on_error=False,
        include_statuses=("new", "needs_retry"),
        runner=runner,
    )
    args = build_parser().parse_args(
        ["worker", "--limit", "7", "--include-status", "new,needs_retry"]
    )

    assert runner.calls == [(1, False), (2, False)]
    assert args.command == "worker"
    assert args.limit == 7
    assert args.include_status == ("new", "needs_retry")
