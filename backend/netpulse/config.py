"""Configuracao do NetPulse, lida de variaveis de ambiente ou de um arquivo .env."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    """Origem dos dados de coleta."""

    DEMO = "demo"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Mode = Field(default=Mode.DEMO, validation_alias="NETPULSE_MODE")
    database_url: str = Field(
        default="sqlite:///./data/netpulse.db",
        validation_alias="NETPULSE_DATABASE_URL",
    )
    max_concurrency: int = Field(default=32, ge=1, validation_alias="NETPULSE_MAX_CONCURRENCY")
    default_interval: int = Field(default=60, ge=1, validation_alias="NETPULSE_DEFAULT_INTERVAL")
    failure_threshold: int = Field(default=3, ge=1, validation_alias="NETPULSE_FAILURE_THRESHOLD")
    correlation_window: int = Field(
        default=180, ge=0, validation_alias="NETPULSE_CORRELATION_WINDOW"
    )
    frontend_dir: str | None = Field(default=None, validation_alias="NETPULSE_FRONTEND_DIR")

    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    ai_model: str = Field(default="claude-sonnet-5", validation_alias="NETPULSE_AI_MODEL")

    @property
    def ai_enabled(self) -> bool:
        """A analise por IA e opcional: sem chave, o resto do sistema segue funcionando."""
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
