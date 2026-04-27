import asyncio
import os
import time
from pathlib import Path

import httpx

from utils import get_task_logger, request_with_retry


SUCCESS_DELAY_SECONDS = 20


class TranscriptionRateLimiter:
    def __init__(self, delay_seconds: int = SUCCESS_DELAY_SECONDS) -> None:
        self.delay_seconds = delay_seconds
        self._lock = asyncio.Lock()
        self._last_success_at = 0.0

    async def transcribe(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        chunk_path: Path,
        chunk_index: int,
    ) -> str:
        async with self._lock:
            logger = get_task_logger(task_id, chunk_index)
            now = time.monotonic()
            elapsed = now - self._last_success_at
            wait_seconds = max(0.0, self.delay_seconds - elapsed)

            if self._last_success_at > 0 and wait_seconds > 0:
                logger.info(
                    "waiting %.1fs before next transcription request",
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

            text = await transcribe_chunk(client, task_id, chunk_path, chunk_index)
            self._last_success_at = time.monotonic()
            return text


async def transcribe_chunk(
    client: httpx.AsyncClient,
    task_id: str,
    chunk_path: Path,
    chunk_index: int,
) -> str:
    logger = get_task_logger(task_id, chunk_index)
    transcription_url = os.getenv("TRANSCRIPTION_API_URL")
    transcription_model = os.getenv("TRANSCRIPTION_MODEL")

    if not transcription_url:
        raise RuntimeError("TRANSCRIPTION_API_URL is not set")
    if not transcription_model:
        raise RuntimeError("TRANSCRIPTION_MODEL is not set")

    logger.info("transcribing chunk from %s", chunk_path.name)

    with chunk_path.open("rb") as audio_file:
        files = {"file": (chunk_path.name, audio_file, "audio/ogg")}
        data = {
            "model": transcription_model,
            "response_format": "text",
        }
        response = await request_with_retry(
            client,
            "POST",
            transcription_url,
            task_id=task_id,
            rate_limit_header="x-ratelimit-reset-audio-seconds",
            files=files,
            data=data,
        )

    text = response.text.strip()
    logger.info("chunk transcribed")
    return text
