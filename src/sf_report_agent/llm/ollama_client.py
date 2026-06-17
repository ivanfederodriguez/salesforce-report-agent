from __future__ import annotations

import json
from typing import Any

import requests


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float = 0,
        timeout: float = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama no responde en {self.base_url}: {exc}") from exc
        payload: dict[str, Any] = response.json()
        return payload

    def complete_json(self, *, system: str, prompt: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/generate", json=body, timeout=self.timeout
            )
            response.raise_for_status()
            raw = response.json().get("response", "{}")
            parsed = json.loads(raw)
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise OllamaError(f"Falló la respuesta JSON de Ollama: {exc}") from exc
        if not isinstance(parsed, dict):
            raise OllamaError("Ollama devolvió JSON que no es un objeto")
        return parsed

