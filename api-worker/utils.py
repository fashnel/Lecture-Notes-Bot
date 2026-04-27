import asyncio
import json
import logging
import math
import re
import shutil
from pathlib import Path
from typing import Any

import httpx


class TaskContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = "-"
        if not hasattr(record, "chunk_index"):
            record.chunk_index = "-"
        return True


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s [%(name)s] "
            "[task_id=%(task_id)s] [chunk=%(chunk_index)s] %(message)s"
        ),
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(TaskContextFilter())


def get_task_logger(task_id: str, chunk_index: int | None = None) -> logging.LoggerAdapter:
    logger = logging.getLogger("api-worker")
    return logging.LoggerAdapter(
        logger,
        {
            "task_id": task_id,
            "chunk_index": "-" if chunk_index is None else chunk_index,
        },
    )


def ensure_directories(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def sync_prompt_directory(source_dir: Path, target_dir: Path) -> int:
    ensure_directories(target_dir)
    copied_files = 0

    if not source_dir.exists():
        return copied_files

    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file():
            continue

        relative_path = source_path.relative_to(source_dir)
        target_path = target_dir / relative_path
        ensure_directories(target_path.parent)
        shutil.copy2(source_path, target_path)
        copied_files += 1

    return copied_files


def sync_prompt_directories(source_dirs: list[Path], target_dir: Path) -> tuple[int, Path | None]:
    for source_dir in source_dirs:
        copied_files = sync_prompt_directory(source_dir, target_dir)
        if copied_files > 0:
            return copied_files, source_dir
    return 0, None


def clean_response(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:]).strip()

    if cleaned.endswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[:-1]).strip()

    return cleaned


def parse_reset_time(value: str) -> int:
    if not value:
        return 0

    normalized_value = value.strip()

    milliseconds_match = re.fullmatch(r"(\d+(?:\.\d+)?)ms", normalized_value)
    if milliseconds_match:
        milliseconds = float(milliseconds_match.group(1))
        return max(1, math.ceil(milliseconds / 1000))

    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", normalized_value)
    if not match:
        raise ValueError(f"invalid reset time format: {value}")

    minutes = int(match.group(1) or 0)
    seconds = float(match.group(2) or 0)
    return math.ceil(minutes * 60 + seconds)


def load_prompt(prompt_name: str) -> str:
    prompt_path = Path(prompt_name)
    prompt_filename = prompt_path.name
    candidate_paths = [
        prompt_path,
        Path("prompts") / prompt_name,
        Path("prompts") / prompt_filename,
        Path(__file__).resolve().parent / prompt_name,
        Path(__file__).resolve().parent / "prompts" / prompt_filename,
        Path(__file__).resolve().parent.parent / prompt_name,
        Path(__file__).resolve().parent.parent / "prompts" / prompt_filename,
    ]
    for prompt_path in candidate_paths:
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"prompt file not found: {prompt_name}")


def get_transcription_cache_path(results_dir: Path, task_id: str) -> Path:
    return results_dir / f"{task_id}.transcriptions.json"


def read_transcription_cache(results_dir: Path, task_id: str) -> dict[int, str]:
    cache_path = get_transcription_cache_path(results_dir, task_id)
    if not cache_path.exists():
        return {}

    raw_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return {int(key): value for key, value in raw_payload.items()}


def write_transcription_cache(results_dir: Path, task_id: str, transcriptions: dict[int, str]) -> None:
    cache_path = get_transcription_cache_path(results_dir, task_id)
    payload = {str(key): value for key, value in sorted(transcriptions.items())}
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_status(status_dir: Path, task_id: str) -> dict[str, Any]:
    status_path = status_dir / f"{task_id}.json"
    return json.loads(status_path.read_text(encoding="utf-8"))


def write_status(
    status_dir: Path,
    task_id: str,
    status: str,
    total_chunks: int,
    ready_chunks: int,
    error: str | None = None,
) -> None:
    status_path = status_dir / f"{task_id}.json"
    payload = {
        "status": status,
        "total_chunks": total_chunks,
        "ready_chunks": ready_chunks,
        "error": error,
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    task_id: str,
    rate_limit_header: str,
    **kwargs: Any,
) -> httpx.Response:
    logger = get_task_logger(task_id)
    attempt = 0
    consecutive_500 = 0

    while attempt < 10:
        attempt += 1
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            wait_seconds = min(attempt * 2, 20)
            logger.warning(
                "Request transport error on attempt %s/10, waiting %ss: %s",
                attempt,
                wait_seconds,
                exc,
            )
            if attempt >= 10:
                raise RuntimeError(f"request transport error for {url}: {exc}") from exc
            await asyncio.sleep(wait_seconds)
            continue

        if response.status_code < 400:
            consecutive_500 = 0
            return response

        if response.status_code == 429:
            reset_value = response.headers.get(rate_limit_header, "0s")
            seconds = parse_reset_time(reset_value)
            logger.warning("Rate limit hit, waiting %ss", seconds)
            await asyncio.sleep(seconds + 1)
            continue

        if response.status_code == 500:
            consecutive_500 += 1
            logger.warning("Server error 500, waiting 20s")
            if consecutive_500 >= 3:
                raise RuntimeError(f"server error 500 repeated 3 times for {url}")
            await asyncio.sleep(20)
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"request failed with status {response.status_code}: {response.text}"
            ) from exc

    raise RuntimeError(f"request retry limit exceeded for {url}")
