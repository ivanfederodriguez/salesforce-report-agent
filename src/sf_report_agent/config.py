from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Valor booleano inválido: {value!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    source_db_path: Path
    worker_db_path: Path
    artifacts_dir: Path
    field_mapping_path: Path | None
    model_provider: str
    ollama_model: str
    ollama_base_url: str
    ollama_temperature: float
    salesforce_username: str | None
    salesforce_password: str | None
    salesforce_security_token: str | None
    salesforce_domain: str
    sf_read_only: bool
    max_export_rows: int
    require_human_approval_for_pii: bool
    log_pii: bool
    update_source_task: bool

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        load_dotenv(dotenv_path=env_file, override=False)
        mapping_value = os.getenv("FIELD_MAPPING_PATH", "").strip()
        settings = cls(
            source_db_path=Path(os.getenv("SOURCE_DB_PATH", "../slack-automatizacion/slack_agent.db")),
            worker_db_path=Path(os.getenv("WORKER_DB_PATH", "salesforce_report_agent.db")),
            artifacts_dir=Path(os.getenv("ARTIFACTS_DIR", "artifacts")),
            field_mapping_path=Path(mapping_value) if mapping_value else None,
            model_provider=os.getenv("MODEL_PROVIDER", "ollama"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:e2b-mlx"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0")),
            salesforce_username=os.getenv("SALESFORCE_USERNAME") or None,
            salesforce_password=os.getenv("SALESFORCE_PASSWORD") or None,
            salesforce_security_token=os.getenv("SALESFORCE_SECURITY_TOKEN") or None,
            salesforce_domain=os.getenv("SALESFORCE_DOMAIN", "login"),
            sf_read_only=_as_bool(os.getenv("SF_READ_ONLY"), True),
            max_export_rows=int(os.getenv("MAX_EXPORT_ROWS", "50000")),
            require_human_approval_for_pii=_as_bool(
                os.getenv("REQUIRE_HUMAN_APPROVAL_FOR_PII"), True
            ),
            log_pii=_as_bool(os.getenv("LOG_PII"), False),
            update_source_task=_as_bool(os.getenv("UPDATE_SOURCE_TASK"), False),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.model_provider != "ollama":
            raise ValueError("El MVP solo admite MODEL_PROVIDER=ollama")
        if not self.sf_read_only:
            raise ValueError("SF_READ_ONLY debe permanecer en true en el MVP")
        if self.max_export_rows <= 0:
            raise ValueError("MAX_EXPORT_ROWS debe ser mayor que cero")

    @property
    def has_salesforce_credentials(self) -> bool:
        return bool(
            self.salesforce_username
            and self.salesforce_password
            and self.salesforce_security_token
        )

