"""Application configuration management using Pydantic Settings."""

import json
from datetime import datetime
from typing import Dict, Optional, Union
from zoneinfo import ZoneInfo
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for AI Task Assistant."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database
    database_url: str = Field(default="sqlite:///./tasks.db", validation_alias="DATABASE_URL")

    # Intent extraction
    intent_mode: str = Field(default="deterministic", validation_alias="INTENT_MODE")
    llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
    llm_api_key: Optional[str] = Field(default=None, validation_alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")

    # Timezone & reference clock
    timezone: str = Field(default="Asia/Riyadh", validation_alias="TIMEZONE")
    reference_datetime_override: Optional[str] = Field(
        default=None, validation_alias="REFERENCE_DATETIME_OVERRIDE"
    )


    # Archival configuration
    task_archive_retention_days: int = Field(default=30, validation_alias="TASK_ARCHIVE_RETENTION_DAYS")

    # Workday and Weekend Policy Configuration (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
    company_weekend_days: list[int] = Field(default_factory=lambda: [4], validation_alias="COMPANY_WEEKEND_DAYS")

    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("company_weekend_days", mode="before")
    @classmethod
    def parse_company_weekend_days(cls, value: Union[str, list, None]) -> list[int]:
        """Parse weekend days list from comma-separated string, JSON, or list."""
        if value is None:
            return [4]
        if isinstance(value, str):
            clean = value.strip()
            if clean.startswith("[") and clean.endswith("]"):
                try:
                    return [int(x) for x in json.loads(clean)]
                except Exception:
                    pass
            parts = [p.strip() for p in clean.split(",") if p.strip()]
            return [int(p) for p in parts if p.isdigit()]
        if isinstance(value, list):
            return [int(x) for x in value]
        return [4]


    def get_timezone(self) -> ZoneInfo:
        """Return the configured ZoneInfo object."""
        return ZoneInfo(self.timezone)

    def get_reference_datetime(self) -> datetime:
        """Return the current reference datetime (either overridden or real current time in Riyadh)."""
        tz = self.get_timezone()
        if self.reference_datetime_override:
            try:
                # Try standard ISO / space-separated formats
                clean_str = self.reference_datetime_override.strip()
                if "T" in clean_str:
                    dt = datetime.fromisoformat(clean_str)
                else:
                    dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=tz)
                return dt.astimezone(tz)
            except Exception:
                pass
        return datetime.now(tz)


settings = Settings()
