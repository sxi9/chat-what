from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # WhatsApp Cloud API
    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    wa_verify_token: str = "verify-me"
    wa_app_secret: str = ""
    wa_api_version: str = "v21.0"

    wa_template_job_alert: str = "job_alert"
    wa_template_status_update: str = "application_status"
    wa_template_ticket_reply: str = "ticket_reply"
    wa_template_reminder: str = "reminder"
    wa_template_language: str = "en"

    # Backend
    database_url: str = "sqlite:///./jobbot.db"
    admin_api_key: str = "change-me"
    alerts_per_user_per_run: int = 3
    alert_lookback_days: int = 14
    recommendations_limit: int = 5
    faq_file: str = str(Path(__file__).with_name("data") / "faq.json")

    # Natural-language assistant (Claude). The SDK reads ANTHROPIC_API_KEY from the environment.
    anthropic_api_key: str = ""
    assistant_enabled: bool | None = None  # None = on when an API key is present
    assistant_model: str = "claude-opus-5-5"
    assistant_effort: str = "medium"
    assistant_max_tokens: int = 2048
    assistant_history_turns: int = 20
    assistant_web_search: bool = True
    assistant_name: str = "JobBot"

    @property
    def assistant_on(self) -> bool:
        if self.assistant_enabled is not None:
            return self.assistant_enabled
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
