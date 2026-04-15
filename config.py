"""
Конфигурация приложения через Pydantic BaseSettings.
Все настройки считываются из переменных окружения.
"""

from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # DeepSeek API
    deepseek_api_key: str = Field(..., description="API ключ для DeepSeek")
    deepseek_api_url: str = Field(
        default="https://api.deepseek.com/v1/chat/completions",
        description="URL API DeepSeek",
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        description="Модель DeepSeek для запросов",
    )

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

    # Whisper
    whisper_model_size: str = Field(
        default="tiny",
        description="Размер модели Whisper",
    )
    whisper_device: str = Field(
        default="cpu",
        description="Устройство для Whisper (cpu/cuda)",
    )
    whisper_compute_type: str = Field(
        default="int8",
        description="Тип вычислений Whisper",
    )
    whisper_cpu_threads: int = Field(
        default=2,
        description="Количество потоков CPU для Whisper",
    )

    # LLM Prompt
    llm_system_prompt: str = Field(
        default=(
            "Сделай структурированный конспект лекции по Алгоритмам и "
            "Структурам Данных / Linux на русском языке в формате Markdown. "
            "Используй заголовки, списки, выделения. Сохраняй ключевые "
            "формулы, примеры кода и термины."
        ),
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
