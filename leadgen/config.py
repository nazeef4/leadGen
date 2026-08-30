"""Runtime configuration.

Everything is file/env driven so the app stays fully local.  Nothing is sent to a
third party unless the user explicitly configures an LLM endpoint.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = Path(
    __import__("os").environ.get("LEADGEN_STATE_DIR") or PROJECT_ROOT / "leadgen_state"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEADGEN_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app / server -----------------------------------------------------
    app_name: str = "LeadGen Studio"
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False
    state_dir: Path = DEFAULT_STATE_DIR

    # --- storage ----------------------------------------------------------
    database_url: str | None = None  # defaults to <state_dir>/leadgen.db

    # --- anti-spam / dispatch guardrails ----------------------------------
    # Google Workspace free (consumer) accounts: 500 recipients / day.
    # We default to a hard cap of 400 to stay comfortably under the ceiling.
    daily_recipient_cap: int = 400
    hourly_recipient_cap: int = 60
    min_delay_seconds: int = 45
    max_delay_seconds: int = 240
    long_pause_every: int = 12  # take a longer break after N sends
    long_pause_min_seconds: int = 600
    long_pause_max_seconds: int = 1500
    max_consecutive_failures: int = 5  # trip the circuit breaker
    quiet_hours_start: int = 20  # local hour, 24h clock
    quiet_hours_end: int = 8
    enforce_quiet_hours: bool = False
    warmup_day1: int = 20
    warmup_daily_step: int = 25

    # --- LLM (optional) ---------------------------------------------------
    llm_provider: str = "offline"  # offline | openai | openai_compatible | anthropic
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 60

    # --- scraping ---------------------------------------------------------
    scrape_results_per_query: int = 20
    scrape_max_pages_per_campaign: int = 60
    scrape_request_timeout: int = 20
    scrape_delay_min: float = 1.5
    scrape_delay_max: float = 4.0
    scrape_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    verify_mx_records: bool = True

    # --- compliance -------------------------------------------------------
    business_name: str = ""
    business_mailing_address: str = ""
    unsubscribe_url: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.state_dir / 'leadgen.db').as_posix()}"

    @property
    def key_file(self) -> Path:
        return self.state_dir / "secret.key"

    def export_public(self) -> dict:
        """Config values safe to expose to the browser (never the API key)."""
        return {
            "app_name": self.app_name,
            "version": self.version,
            "daily_recipient_cap": self.daily_recipient_cap,
            "hourly_recipient_cap": self.hourly_recipient_cap,
            "min_delay_seconds": self.min_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
            "long_pause_every": self.long_pause_every,
            "enforce_quiet_hours": self.enforce_quiet_hours,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_configured": bool(self.llm_api_key),
            "verify_mx_records": self.verify_mx_records,
            "scrape_results_per_query": self.scrape_results_per_query,
            "business_name": self.business_name,
            "business_mailing_address": self.business_mailing_address,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class JsonStore:
    """Tiny key/value JSON store for things that are not relational (UI prefs etc.)."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def update(self, **kwargs) -> dict:
        data = self.read()
        data.update(kwargs)
        self.write(data)
        return data


__all__ = ["Settings", "get_settings", "JsonStore", "PROJECT_ROOT", "DEFAULT_STATE_DIR"]
