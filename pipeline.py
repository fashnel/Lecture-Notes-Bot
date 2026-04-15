"""
Pipeline обработки видеолекции в PDF-конспект.

Этапы:
1. Извлечение аудио из видео через FFmpeg
2. Транскрибация через Faster-Whisper
3. Отправка текста в DeepSeek API для создания конспекта
4. Генерация PDF из Markdown
"""

import logging
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import markdown2
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from faster_whisper import WhisperModel
from weasyprint import HTML

from config import Settings

logger = logging.getLogger("worker")


class LecturePipeline:
    """Пайплайн обработки видеолекции в PDF."""

    def __init__(self, config: Settings):
        self.config = config
        self._whisper_model = None

    def _load_whisper_model(self) -> WhisperModel:
        """Загрузить модель Whisper (ленивая инициализация)."""
        if self._whisper_model is None:
            logger.info(
                "Загрузка Whisper модели: %s (device=%s, compute_type=%s, threads=%d)",
                self.config.whisper_model_size,
                self.config.whisper_device,
                self.config.whisper_compute_type,
                self.config.whisper_cpu_threads,
            )
            self._whisper_model = WhisperModel(
                self.config.whisper_model_size,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
                cpu_threads=self.config.whisper_cpu_threads,
            )
        return self._whisper_model

    def extract_audio(self, video_path: Path) -> Path:
        """
        Извлечь аудио из видео в WAV (16kHz, mono) через FFmpeg.
        После успешного извлечения исходное видео удаляется.
        """
        start = time.time()
        wav_path = self.config.temp_dir / f"{video_path.stem}.wav"

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            str(wav_path),
        ]

        logger.info("Извлечение аудио: %s -> %s", video_path.name, wav_path.name)
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("FFmpeg ошибка:\n%s", e.stderr)
            raise RuntimeError(f"Не удалось извлечь аудио: {e.stderr}")

        # Удалить исходное видео для экономии места
        video_path.unlink()
        logger.info(
            "Исходное видео удалено: %s (затрачено: %.1f сек)",
            video_path.name,
            time.time() - start,
        )
        return wav_path

    def transcribe_audio(self, wav_path: Path) -> str:
        """Транскрибировать аудио через Faster-Whisper."""
        start = time.time()
        logger.info("Транскрибация аудио: %s", wav_path.name)

        model = self._load_whisper_model()

        segments, info = model.transcribe(
            str(wav_path),
            language="ru",
            beam_size=5,
            vad_filter=True,
        )

        logger.info(
            "Определённый язык: %s (вероятность: %.2f)",
            info.language,
            info.language_probability,
        )

        full_text = []
        for segment in segments:
            full_text.append(segment.text.strip())

        result = " ".join(full_text)
        logger.info(
            "Транскрибация завершена: %d символов (затрачено: %.1f сек)",
            len(result),
            time.time() - start,
        )
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ConnectionError)),
        reraise=True,
    )
    def _call_llm_api(self, text: str) -> str:
        """
        Отправить текст в DeepSeek API и получить Markdown-конспект.
        Использует tenacity для retry при ошибках сети.
        """
        headers = {
            "Authorization": f"Bearer {self.config.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.deepseek_model,
            "messages": [
                {"role": "system", "content": self.config.llm_system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        logger.info("Запрос к DeepSeek API...")
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                self.config.deepseek_api_url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        markdown_text = data["choices"][0]["message"]["content"]
        logger.info("Получен ответ от DeepSeek API: %d символов", len(markdown_text))
        return markdown_text

    def generate_summary(self, transcript: str) -> str:
        """Создать конспект через LLM."""
        start = time.time()
        logger.info("Генерация конспекта через LLM...")

        # Если текст слишком длинный, обрезаем до лимита контекста
        # DeepSeek обычно поддерживает ~8K-32K tokens
        max_chars = 25000
        if len(transcript) > max_chars:
            logger.warning(
                "Транскрипт слишком длинный (%d символов), обрезка до %d",
                len(transcript),
                max_chars,
            )
            transcript = transcript[:max_chars]

        markdown = self._call_llm_api(transcript)
        logger.info(
            "Конспект создан (затрачено: %.1f сек)",
            time.time() - start,
        )
        return markdown

    def generate_pdf(self, markdown: str, output_path: Path) -> Path:
        """Сконвертировать Markdown в PDF через markdown2 + WeasyPrint."""
        start = time.time()
        logger.info("Генерация PDF: %s", output_path.name)

        # Конвертация Markdown -> HTML
        html_content = markdown2.markdown(
            markdown,
            extras=[
                "tables",
                "code-friendly",
                "fenced-code-blocks",
                "header-ids",
                "toc",
            ],
        )

        # Полный HTML с базовыми стилями
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{
                    size: A4;
                    margin: 2cm;
                }}
                body {{
                    font-family: 'DejaVu Sans', Arial, sans-serif;
                    font-size: 12pt;
                    line-height: 1.6;
                    color: #333;
                }}
                h1 {{ font-size: 20pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 4px; }}
                h2 {{ font-size: 16pt; color: #2a2a2a; border-bottom: 1px solid #999; padding-bottom: 3px; }}
                h3 {{ font-size: 14pt; color: #3a3a3a; }}
                code {{
                    background-color: #f4f4f4;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'DejaVu Sans Mono', monospace;
                    font-size: 10pt;
                }}
                pre {{
                    background-color: #f4f4f4;
                    padding: 12px;
                    border-radius: 5px;
                    overflow-x: auto;
                }}
                pre code {{
                    background-color: transparent;
                    padding: 0;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 12px 0;
                }}
                th, td {{
                    border: 1px solid #ccc;
                    padding: 8px;
                    text-align: left;
                }}
                th {{
                    background-color: #f0f0f0;
                    font-weight: bold;
                }}
                blockquote {{
                    border-left: 4px solid #ccc;
                    margin: 12px 0;
                    padding-left: 16px;
                    color: #555;
                }}
                ul, ol {{
                    padding-left: 24px;
                }}
                li {{
                    margin: 4px 0;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        HTML(string=full_html).write_pdf(str(output_path))

        logger.info(
            "PDF создан: %s (%.1f сек, %.1f KB)",
            output_path.name,
            time.time() - start,
            output_path.stat().st_size / 1024,
        )
        return output_path

    def cleanup(self, *paths: Path) -> None:
        """Удалить временные файлы."""
        for path in paths:
            if path.exists():
                try:
                    path.unlink()
                    logger.info("Временный файл удалён: %s", path.name)
                except OSError as e:
                    logger.warning("Не удалось удалить %s: %s", path.name, e)

    def process(self, video_path: Path) -> Path:
        """
        Полный пайплайн: видео -> аудио -> транскрипт -> конспект -> PDF.
        Возвращает путь к готовому PDF.
        """
        total_start = time.time()
        logger.info("=" * 60)
        logger.info("Начало обработки: %s", video_path.name)
        logger.info("=" * 60)

        wav_path = None
        txt_path = None

        try:
            # Шаг 1: Извлечение аудио
            wav_path = self.extract_audio(video_path)

            # Шаг 2: Транскрибация
            transcript = self.transcribe_audio(wav_path)

            # Сохранить транскрипт как txt (для отладки)
            txt_path = self.config.temp_dir / f"{video_path.stem}.txt"
            txt_path.write_text(transcript, encoding="utf-8")

            # Шаг 3: Генерация конспекта через LLM
            markdown = self.generate_summary(transcript)

            # Шаг 4: Генерация PDF
            pdf_path = self.config.output_dir / f"{video_path.stem}.pdf"
            self.generate_pdf(markdown, pdf_path)

            total_time = time.time() - total_start
            logger.info("=" * 60)
            logger.info(
                "Обработка завершена: %s -> %s (всего: %.1f сек / %.1f мин)",
                video_path.name,
                pdf_path.name,
                total_time,
                total_time / 60,
            )
            logger.info("=" * 60)

            return pdf_path

        finally:
            # Очистка временных файлов
            self.cleanup(wav_path, txt_path)
