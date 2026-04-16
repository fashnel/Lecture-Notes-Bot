"""
Конфигурация приложения через Pydantic BaseSettings.
Все настройки считываются из переменных окружения.
"""

import logging
from pathlib import Path
from typing import Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("worker")


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Transcription API (Whisper/Groq/etc)
    transcription_api_key: str = Field(..., description="API ключ для транскрибации")
    transcription_api_url: str = Field(..., description="URL API для транскрибации")
    transcription_model: str = Field(..., description="Модель для транскрибации")

    # LLM API (Summarization)
    llm_api_key: str = Field(..., description="API ключ для LLM")
    llm_api_url: str = Field(..., description="URL API для LLM")
    llm_model: str = Field(..., description="Модель LLM для запросов")

    # Пути
    incoming_dir: Path = Field(
        default=Path("/data/incoming"),
        description="Директория для входящих файлов",
    )
    output_dir: Path = Field(
        default=Path("/data/output"),
        description="Директория для готовых PDF",
    )
    temp_dir: Path = Field(
        default=Path("/data/temp"),
        description="Директория для временных файлов",
    )

    # LLM Prompt
    llm_system_prompt: str = Field(
        default="Сделай подробный конспект лекции в Markdown",
        description="Системный промпт для LLM",
    )

    # Tenacity (retry)
    max_retries: int = Field(
        default=3,
        description="Максимальное количество попыток запроса к API",
    )
    retry_wait_seconds: int = Field(
        default=5,
        description="Базовое время ожидания между попытками (сек)",
    )

    @field_validator("incoming_dir", "output_dir", "temp_dir", mode="before")
    @classmethod
    def resolve_path(cls, v):
        return Path(v)

    def ensure_directories(self) -> None:
        """Создать директории, если они не существуют."""
        for dir_path in [self.incoming_dir, self.output_dir, self.temp_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    def log_config(self) -> None:
        """Логировать текущую конфигурацию с маскированием ключей."""
        logger.debug("Загруженная конфигурация:")
        for field_name, value in self.model_dump().items():
            if "api_key" in field_name.lower():
                masked_value = "****"
                if isinstance(value, str) and len(value) > 8:
                    masked_value = f"{value[:4]}...{value[-4:]}"
                logger.debug("  %s: %s", field_name.upper(), masked_value)
            else:
                logger.debug("  %s: %s", field_name.upper(), value)
