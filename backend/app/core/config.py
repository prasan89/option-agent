from __future__ import annotations

import json
import os
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


def _first_service_credentials(services: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
    for name in names:
        entries = services.get(name)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("credentials"), dict):
                    return entry["credentials"]
    for entries in services.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("name") or "").lower()
            tags = " ".join(str(x).lower() for x in (entry.get("tags") or []))
            if any(name in f"{label} {tags}" for name in names) and isinstance(entry.get("credentials"), dict):
                return entry["credentials"]
    return None


def _credential_url(credentials: dict[str, Any]) -> str | None:
    for key in ("uri", "url", "jdbcUrl", "jdbc_url"):
        if credentials.get(key):
            return str(credentials[key])
    host = credentials.get("hostname") or credentials.get("host")
    port = credentials.get("port")
    database = credentials.get("database") or credentials.get("dbname")
    username = credentials.get("username") or credentials.get("user")
    password = credentials.get("password")
    if host and port and database and username is not None and password is not None:
        from urllib.parse import quote_plus
        return f"postgresql://{quote_plus(str(username))}:{quote_plus(str(password))}@{host}:{port}/{database}"
    return None


def _bound_service_url(names: tuple[str, ...]) -> str | None:
    raw = os.getenv("VCAP_SERVICES", "")
    if not raw:
        return None
    try:
        services = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(services, dict):
        return None
    credentials = _first_service_credentials(services, names)
    return _credential_url(credentials) if credentials else None


class Settings(BaseSettings):
    app_name: str = "option-agent"
    app_version: str = "0.5.0"
    environment: str = "dev"
    log_level: str = "INFO"
    database_url: str = ""
    auto_start_pipeline: bool = True
    groww_access_token: str = ""
    groww_api_key: str = ""
    groww_api_secret: str = ""
    llm_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        if not self.database_url:
            self.database_url = _bound_service_url(("postgres", "postgresql", "elephantsql")) or ""


settings = Settings()
