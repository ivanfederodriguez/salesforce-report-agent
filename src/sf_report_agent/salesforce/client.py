from __future__ import annotations

from typing import Any

from simple_salesforce.api import Salesforce
from simple_salesforce.format import format_soql

from sf_report_agent.salesforce.oauth import (
    SalesforceOAuthError,
    refresh_access_token,
    sanitize_secrets,
)


class SalesforceClientError(RuntimeError):
    pass


class SalesforceClient:
    """Fachada deliberadamente limitada a operaciones de lectura."""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        security_token: str,
        domain: str = "login",
    ) -> None:
        self.username: str | None = username
        self._instance_url: str | None = None
        try:
            self._sf = Salesforce(
                username=username,
                password=password,
                security_token=security_token,
                domain=domain,
            )
        except Exception as exc:
            message = sanitize_secrets(str(exc), password, security_token)
            raise SalesforceClientError(
                f"No fue posible iniciar sesión en Salesforce: {message}"
            ) from exc

    @classmethod
    def from_password(
        cls,
        *,
        username: str,
        password: str,
        security_token: str,
        domain: str = "login",
    ) -> SalesforceClient:
        return cls(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain,
        )

    @classmethod
    def from_session(
        cls,
        *,
        instance_url: str,
        access_token: str,
        username: str | None = None,
    ) -> SalesforceClient:
        try:
            salesforce = Salesforce(instance_url=instance_url, session_id=access_token)
        except Exception as exc:
            message = sanitize_secrets(str(exc), access_token)
            raise SalesforceClientError(
                f"No fue posible crear la sesión OAuth de Salesforce: {message}"
            ) from exc
        client = cls.__new__(cls)
        client._sf = salesforce
        client.username = username
        client._instance_url = instance_url.rstrip("/")
        return client

    @classmethod
    def from_refresh_token(
        cls,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        instance_url: str | None = None,
        username: str | None = None,
    ) -> SalesforceClient:
        try:
            token = refresh_access_token(
                domain=domain,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                instance_url=instance_url,
            )
        except SalesforceOAuthError as exc:
            raise SalesforceClientError(str(exc)) from exc
        return cls.from_session(
            instance_url=token.instance_url,
            access_token=token.access_token,
            username=username,
        )

    @property
    def instance_url(self) -> str | None:
        if self._instance_url:
            return self._instance_url
        base_url = getattr(self._sf, "base_url", None)
        if not base_url:
            return None
        marker = "/services/"
        return str(base_url).split(marker, maxsplit=1)[0]

    def query_all(self, soql: str) -> list[dict[str, Any]]:
        try:
            result = self._sf.query_all(soql)
        except Exception as exc:
            raise SalesforceClientError(f"Falló la consulta SOQL de lectura: {exc}") from exc
        records = result.get("records", [])
        return [dict(record) for record in records]

    def describe_global(self) -> dict[str, Any]:
        try:
            result = self._sf.describe()
            if not isinstance(result, dict):
                raise TypeError("describe_global no devolvió un objeto")
            return result
        except Exception as exc:
            raise SalesforceClientError(f"Falló describe_global: {exc}") from exc

    def describe_object(self, object_name: str) -> dict[str, Any]:
        try:
            return dict(getattr(self._sf, object_name).describe())
        except Exception as exc:
            raise SalesforceClientError(f"No se pudo describir {object_name}: {exc}") from exc

    def get_campaigns_by_ids(self, campaign_ids: list[str]) -> list[dict[str, Any]]:
        if not campaign_ids:
            return []
        query = format_soql(
            "SELECT Id, Name, IsActive FROM Campaign WHERE Id IN {ids}", ids=campaign_ids
        )
        return self.query_all(query)

    def test_query(self, object_name: str) -> bool:
        self.query_all(f"SELECT Id FROM {object_name} LIMIT 1")
        return True
