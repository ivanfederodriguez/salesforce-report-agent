CREATE TABLE IF NOT EXISTS report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    request_json TEXT,
    plan_json TEXT,
    permission_report_json TEXT,
    soql TEXT,
    row_count INTEGER,
    response_text TEXT,
    warnings_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS report_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES report_runs(id)
);

CREATE TABLE IF NOT EXISTS report_run_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    variant_id TEXT NOT NULL,
    variant_label TEXT NOT NULL,
    interpretation TEXT,
    soql TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    artifacts_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, variant_id),
    FOREIGN KEY(run_id) REFERENCES report_runs(id)
);
