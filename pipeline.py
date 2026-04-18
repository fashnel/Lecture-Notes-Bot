"""
Pipeline обработки видеолекции в PDF-конспект.

Этапы:
1. Извлечение и сжатие аудио через FFmpeg
2. Транскрибация через API (например, Groq Whisper)
3. Отправка текста в LLM API для создания конспекта
4. Генерация PDF через weasyprint
"""

import logging
import re
import subprocess
import time
import os
from pathlib import Path
from typing import Optional

import httpx
from weasyprint import HTML
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
            "-filter:a", "atempo=1.25",
            "-b:a", "64k",
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

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Получить длительность аудиофайла в секундах с помощью ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.error("DEBUG: Не удалось получить длительность аудио %s: %s", audio_path.name, str(e))
            raise RuntimeError(f"Не удалось получить длительность аудио: {e}")


    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ConnectionError)),
        reraise=True,
    )
    def transcribe_audio(self, mp3_path: Path) -> str:
        """
        Транскрибировать аудио через API транскрибации.
        Если файл больше 18 МБ, разбить на фрагменты, отправлять по очереди с time.sleep(60)
        после каждого успешного ответа.
        """
        start = time.time()
        logger.info("Транскрибация аудио: %s", mp3_path.name)

        # Проверка размера файла (18 МБ)
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        if file_size_mb <= 18:
            logger.debug("DEBUG: Файл MP3 <= 18 МБ, транскрибируем целиком.")
            return self._send_transcription_request(mp3_path)
        else:
            logger.info("DEBUG: Файл MP3 (%.2f МБ) > 18 МБ, разбиваем на фрагменты.", file_size_mb)
            return self._transcribe_large_audio(mp3_path)

    def _transcribe_large_audio(self, mp3_path: Path) -> str:
        """
        Разбивает большой MP3 файл на фрагменты, транскрибирует каждый и объединяет результаты.
        """
        total_transcript = []
        temp_chunks = []
        try:
            duration = self._get_audio_duration(mp3_path)
            # 15 минутные чанки, чтобы быть уверенными, что размер будет меньше 18 МБ
            # 64kbps * 60s/min * 15min / 8 bits/byte = 7.2MB
            chunk_duration_sec = 15 * 60
            num_chunks = max(1, (int(duration / chunk_duration_sec) + (1 if duration % chunk_duration_sec > 0 else 0)))

            logger.info("DEBUG: Аудио будет разбито на %d фрагментов по ~%.0f секунд.", num_chunks, chunk_duration_sec)

            for i in range(num_chunks):
                chunk_start_time = i * chunk_duration_sec
                chunk_output_path = (
                    self.config.temp_dir / f"{mp3_path.stem}_chunk_{i:03d}.mp3"
                )
                chunk_transcript_path = (
                    self.config.temp_dir / f"{mp3_path.stem}_chunk_{i:03d}.txt"
                )
                temp_chunks.append(chunk_output_path)

                if chunk_transcript_path.exists():
                    logger.info("DEBUG: Транскрипция фрагмента %d уже существует, пропускаем: %s", i + 1, chunk_transcript_path.name)
                    total_transcript.append(chunk_transcript_path.read_text(encoding="utf-8"))
                    continue

                logger.info("DEBUG: Извлечение фрагмента %d/%d: %s", i + 1, num_chunks, chunk_output_path.name)
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i", str(mp3_path),
                    "-ss", str(chunk_start_time),
                    "-t", str(chunk_duration_sec),
                    "-c", "copy",
                    str(chunk_output_path),
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)

                logger.info("DEBUG: Транскрибирование фрагмента %d/%d: %s", i + 1, num_chunks, chunk_output_path.name)
                chunk_transcript = self._send_transcription_request(chunk_output_path)
                total_transcript.append(chunk_transcript)

                # Сохраняем транскрипцию фрагмента (чекпоинтинг)
                chunk_transcript_path.write_text(chunk_transcript, encoding="utf-8")
                logger.debug("DEBUG: Транскрипция фрагмента %d сохранена в %s", i + 1, chunk_transcript_path.name)

                # Задержка для обхода Rate Limit
                if i < num_chunks - 1: # Не задерживаемся после последнего фрагмента
                    logger.info("DEBUG: Ожидание 60 секунд для обхода Rate Limit...")
                    time.sleep(60)

            return "".join(total_transcript)
        finally:
            # Очистка временных аудио фрагментов
            for chunk_path in temp_chunks:
                self.cleanup(chunk_path)

    def _send_transcription_request(self, mp3_path: Path) -> str:
        """Отправляет запрос на транскрибацию одного аудиофайла."""
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
        """Создать конспект через LLM, разбивая текст на чанки и обходя Rate Limit."""
        start = time.time()
        logger.info("Генерация конспекта через LLM...")

        full_markdown_summary = []
        # Разбиваем текст на чанки по 30 000 символов
        # Используем max_chars из старой версии, но теперь это chunk_size
        chunk_size = 30000
        
        num_chunks = (len(transcript) + chunk_size - 1) // chunk_size

        if num_chunks > 1:
            logger.info("DEBUG: Транскрипт будет разбит на %d фрагментов по %d символов.", num_chunks, chunk_size)

        for i in range(num_chunks):
            chunk_start = i * chunk_size
            chunk_end = min((i + 1) * chunk_size, len(transcript))
            text_chunk = transcript[chunk_start:chunk_end]
            
            logger.debug("DEBUG: Отправка текстового фрагмента %d/%d (символов: %d) в LLM.", i + 1, num_chunks, len(text_chunk))
            markdown_chunk = self._call_llm_api(text_chunk)
            full_markdown_summary.append(markdown_chunk)

            # Задержка для обхода Rate Limit
            if i < num_chunks - 1: # Не задерживаемся после последнего фрагмента
                logger.info("DEBUG: Ожидание 60 секунд для обхода Rate Limit для LLM...")
                time.sleep(60)

        final_markdown = "\n\n".join(full_markdown_summary)
        logger.info(
            "Конспект создан (затрачено: %.1f сек, символов: %d)",
            time.time() - start,
            len(final_markdown),
        )
        return final_markdown

    def generate_pdf(self, html_content: str, output_path: Path) -> Path:
        """Генерация PDF через WeasyPrint."""
        
        start_time = time.time()
        logger.info("Генерация PDF (WeasyPrint): %s", output_path.name)
        
        try:
            # Рендерим HTML напрямую в файл
            HTML(string=html_content).write_pdf(str(output_path))
            
            logger.info("PDF создан за %.2f сек: %s", time.time() - start_time, output_path.name)
            return output_path
            
        except Exception as e:
            logger.error("Ошибка WeasyPrint: %s", str(e))
            raise

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
