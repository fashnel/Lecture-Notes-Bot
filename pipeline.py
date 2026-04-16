"""
Pipeline обработки видеолекции в PDF-конспект.

Этапы:
1. Извлечение и сжатие аудио через FFmpeg
2. Транскрибация через API (например, Groq Whisper)
3. Отправка текста в LLM API для создания конспекта
4. Генерация PDF через fpdf2
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
from fpdf import FPDF
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import Settings

logger = logging.getLogger("worker")


class LecturePipeline:
    """Пайплайн обработки видеолекции в PDF."""

    def __init__(self, config: Settings):
        self.config = config

    def extract_audio(self, video_path: Path) -> Path:
        """
        Извлечь аудио из видео в MP3 с сильным сжатием.
        Параметры: моно, 16kHz, ускорение 1.5x, битрейт 24k.
        """
        start = time.time()
        mp3_path = self.config.temp_dir / f"{video_path.stem}.mp3"

        if mp3_path.exists():
            logger.info("DEBUG: Файл MP3 уже существует, пропускаем извлечение: %s", mp3_path.name)
            return mp3_path

        logger.debug("DEBUG: Начинаем извлечение аудио из %s", video_path.name)
        # Параметры из ТЗ:
        # -vn: нет видео
        # -ac 1: моно
        # -ar 16000: 16kHz
        # -filter:a "atempo=1.5": ускорение в 1.5 раза
        # -b:a 24k: битрейт 24kbps
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-filter:a", "atempo=1.5",
            "-b:a", "24k",
            str(mp3_path),
        ]

        logger.info("Извлечение и сжатие аудио: %s -> %s", video_path.name, mp3_path.name)
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("DEBUG: Ошибка FFmpeg (тип: %s): %s", type(e).__name__, e.stderr)
            raise RuntimeError(f"Не удалось извлечь аудио: {e.stderr}")

        # Удалить исходное видео для экономии места
        if video_path.exists():
            video_path.unlink()
            logger.info(
                "Исходное видео удалено: %s (затрачено: %.1f сек)",
                video_path.name,
                time.time() - start,
            )
        return mp3_path

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ConnectionError)),
        reraise=True,
    )
    def transcribe_audio(self, mp3_path: Path) -> str:
        """Транскрибировать аудио через API транскрибации."""
        start = time.time()
        logger.info("Транскрибация аудио: %s", mp3_path.name)
        logger.debug("DEBUG: Запрос к API транскрибации: %s", self.config.transcription_api_url)

        headers = {
            "Authorization": f"Bearer {self.config.transcription_api_key}",
        }
        
        files = {
            "file": (mp3_path.name, open(mp3_path, "rb"), "audio/mpeg"),
        }
        data = {
            "model": self.config.transcription_model,
            "language": "ru",
            "response_format": "json",
        }

        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    self.config.transcription_api_url,
                    headers=headers,
                    files=files,
                    data=data,
                )
                response.raise_for_status()
                result_json = response.json()
        except httpx.HTTPError as e:
            logger.error("DEBUG: HTTP ошибка при транскрибации (тип: %s): %s", type(e).__name__, str(e))
            raise
        finally:
            files["file"][1].close()

        transcript = result_json.get("text", "")
        logger.info(
            "Транскрибация завершена: %d символов (затрачено: %.1f сек)",
            len(transcript),
            time.time() - start,
        )
        return transcript

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ConnectionError)),
        reraise=True,
    )
    def _call_llm_api(self, text: str) -> str:
        """
        Отправить текст в LLM API и получить Markdown-конспект.
        """
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": self.config.llm_system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        logger.info("Запрос к LLM API (%s)...", self.config.llm_api_url)
        logger.debug("DEBUG: Отправка %d символов в LLM", len(text))
        
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    self.config.llm_api_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            logger.error("DEBUG: HTTP ошибка при запросе к LLM (тип: %s): %s", type(e).__name__, str(e))
            raise

        markdown_text = data["choices"][0]["message"]["content"]
        logger.info("Получен ответ от LLM API: %d символов", len(markdown_text))
        return markdown_text

    def generate_summary(self, transcript: str) -> str:
        """Создать конспект через LLM."""
        start = time.time()
        logger.info("Генерация конспекта через LLM...")

        # Ограничение контекста
        max_chars = 30000
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
        """Сгенерировать PDF через fpdf2, очистив Markdown от символов."""
        start = time.time()
        logger.info("Генерация PDF: %s", output_path.name)
        logger.debug("DEBUG: Подготовка текста для PDF (очистка Markdown)")

        # Очистка текста от символов #, *, _
        clean_text = re.sub(r'[#*_]', '', markdown)

        pdf = FPDF()
        pdf.add_page()
        
        font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        if Path(font_path).exists():
            pdf.add_font("LiberationSans", "", font_path)
            pdf.set_font("LiberationSans", size=12)
            logger.debug("DEBUG: Используется шрифт LiberationSans")
        else:
            pdf.set_font("Arial", size=12) # Fallback
            logger.debug("DEBUG: Шрифт LiberationSans не найден, используется Arial")
            
        for line in clean_text.split('\n'):
            pdf.multi_cell(0, 10, txt=line)

        pdf.output(str(output_path))

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
            if path and path.exists():
                try:
                    path.unlink()
                    logger.info("Временный файл удалён: %s", path.name)
                except OSError as e:
                    logger.warning("DEBUG: Не удалось удалить %s (тип: %s): %s", path.name, type(e).__name__, e)

    def process(self, video_path: Path) -> Path:
        """
        Полный пайплайн с контрольными точками (checkpointing).
        видео -> аудио -> транскрипт -> конспект -> PDF.
        """
        total_start = time.time()
        logger.info("=" * 60)
        logger.info("Начало обработки: %s", video_path.name)
        logger.info("=" * 60)

        mp3_path = self.config.temp_dir / f"{video_path.stem}.mp3"
        txt_path = self.config.temp_dir / f"{video_path.stem}.txt"

        try:
            # Шаг 1: Извлечение и сжатие аудио
            if not mp3_path.exists():
                logger.debug("DEBUG: Этап 1: Извлечение аудио")
                mp3_path = self.extract_audio(video_path)
            else:
                logger.info("DEBUG: Этап 1 пропущен: MP3 уже существует")
                # Все равно удаляем видео, так как оно больше не нужно
                if video_path.exists():
                    video_path.unlink()
                    logger.debug("DEBUG: Исходное видео удалено (MP3 уже был)")

            # Шаг 2: Транскрибация
            if not txt_path.exists():
                logger.debug("DEBUG: Этап 2: Транскрибация")
                transcript = self.transcribe_audio(mp3_path)
                # Сохранить транскрипт как txt (checkpoint)
                txt_path.write_text(transcript, encoding="utf-8")
                logger.debug("DEBUG: Транскрипт сохранен в %s", txt_path.name)
            else:
                logger.info("DEBUG: Этап 2 пропущен: TXT транскрипт уже существует")
                transcript = txt_path.read_text(encoding="utf-8")

            # Шаг 3: Генерация конспекта
            logger.debug("DEBUG: Этап 3: Генерация конспекта")
            markdown = self.generate_summary(transcript)

            # Шаг 4: Генерация PDF
            logger.debug("DEBUG: Этап 4: Генерация PDF")
            pdf_path = self.config.output_dir / f"{video_path.stem}.pdf"
            self.generate_pdf(markdown, pdf_path)

            total_time = time.time() - total_start
            logger.info("=" * 60)
            logger.info(
                "Обработка завершена: %s -> %s (всего: %.1f сек)",
                video_path.name,
                pdf_path.name,
                total_time,
            )
            logger.info("=" * 60)

            return pdf_path

        except Exception as e:
            logger.error("DEBUG: Ошибка в пайплайне (тип: %s): %s", type(e).__name__, str(e))
            raise
        finally:
            # Очистка временных файлов (можно закомментировать для отладки, 
            # но по ТЗ это не требовалось менять в плане удаления, 
            # хотя checkpointing подразумевает сохранение файлов при сбое.
            # Если мы удаляем их в finally, то checkpointing работает только ПРИ ЖИВОМ процессе или если мы сами их подкладываем.
            # Обычно checkpointing предполагает, что мы НЕ удаляем их, если хотим возобновить.
            # Но в ТЗ сказано "Это позволит тестировать генерацию PDF, просто подкладывая готовый TXT файл".
            # Значит, если файл уже ЕСТЬ, мы его используем.
            # Если мы хотим сохранить файлы для отладки, нам не стоит их удалять в success.
            
            # По умолчанию в исходном коде была очистка. 
            # Но если мы хотим checkpointing между запусками, очистку лучше делать только для mp3 если txt готов.
            # Оставим очистку как была, но пользователь может сам подложить файл в temp_dir.
            pass
            # self.cleanup(mp3_path, txt_path) # Закомментируем очистку, чтобы checkpointing имел смысл между запусками.
            # Или лучше: удаляем mp3 если txt готов, но txt оставляем?
            # В ТЗ не сказано про очистку, но в исходном коде она была.
            # Я закомментирую очистку временных файлов, чтобы checkpointing работал.
