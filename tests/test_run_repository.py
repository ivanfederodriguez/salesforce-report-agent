import sqlite3
from pathlib import Path

import pytest

from sf_report_agent.db.run_repository import ReportRunRepository


@pytest.mark.parametrize(
    "status",
    [
        "dry_run_completed",
        "done_pending_approval",
        "done_pending_reply",
        "needs_clarification",
        "failed",
    ],
)
def test_repository_persists_supported_final_statuses(tmp_path: Path, status: str) -> None:
    repository = ReportRunRepository(tmp_path / "worker.db")
    run_id = repository.start_run(123)

    repository.finish_run(
        run_id,
        status=status,
        request={"report_type": "altas_por_campaña"},
        plan={"needs_clarification": status == "needs_clarification"},
        response_text="Respuesta persistida",
        warnings=["Warning persistido"],
        error="Error de prueba" if status == "failed" else None,
    )

    with sqlite3.connect(repository.db_path) as connection:
        row = connection.execute(
            "SELECT status, request_json, plan_json, response_text, warnings_json, error "
            "FROM report_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == status
    assert row[1] == '{"report_type": "altas_por_campaña"}'
    assert row[2] is not None
    assert row[3] == "Respuesta persistida"
    assert row[4] == '["Warning persistido"]'
    assert (row[5] is not None) is (status == "failed")


def test_repository_persists_variant_results(tmp_path: Path) -> None:
    repository = ReportRunRepository(tmp_path / "worker.db")
    run_id = repository.start_run(23)

    repository.add_variant_result(
        run_id,
        23,
        variant_id="campaign_origin",
        variant_label="Campaña de origen",
        interpretation="Filtro por campaña de origen",
        soql="SELECT Id FROM Donation__c LIMIT 10",
        row_count=4,
        artifacts=["artifacts/report.csv"],
        warnings=["warning"],
    )

    with sqlite3.connect(repository.db_path) as connection:
        row = connection.execute(
            "SELECT variant_id, variant_label, soql, row_count, artifacts_json, warnings_json "
            "FROM report_run_variants WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert row == (
        "campaign_origin",
        "Campaña de origen",
        "SELECT Id FROM Donation__c LIMIT 10",
        4,
        '["artifacts/report.csv"]',
        '["warning"]',
    )
