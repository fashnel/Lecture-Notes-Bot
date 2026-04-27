import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

from summarizer import LlmRateLimiter, generate_html, summarize_transcript
from transcriber import TranscriptionRateLimiter
from utils import (
    ensure_directories,
    get_task_logger,
    read_status,
    read_transcription_cache,
    setup_logging,
    sync_prompt_directories,
    write_status,
    write_transcription_cache,
)


SCAN_INTERVAL_SECONDS = 2


@dataclass
class TaskState:
    task_id: str
    transcriptions: dict[int, str] = field(default_factory=dict)
    in_progress_chunks: set[int] = field(default_factory=set)
    completed: bool = False
    ffmpeg_done_seen: bool = False
    finalizing: bool = False


def build_client() -> httpx.AsyncClient:
    def normalize_proxy_url(value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith("socks5h://"):
            return "socks5://" + value.removeprefix("socks5h://")
        return value

    transcription_api_key = os.getenv("GROQ_API_KEY")
    headers = {}
    if transcription_api_key:
        headers["Authorization"] = f"Bearer {transcription_api_key}"

    http_proxy = normalize_proxy_url(os.getenv("HTTP_PROXY"))
    https_proxy = normalize_proxy_url(os.getenv("HTTPS_PROXY"))
    proxies = None
    if http_proxy or https_proxy:
        proxies = {}
        if http_proxy:
            proxies["http://"] = http_proxy
        if https_proxy:
            proxies["https://"] = https_proxy
        if "https://" not in proxies and "http://" in proxies:
            proxies["https://"] = proxies["http://"]
        if "http://" not in proxies and "https://" in proxies:
            proxies["http://"] = proxies["https://"]

    return httpx.AsyncClient(headers=headers, proxies=proxies, timeout=300.0)


def get_chunk_path(chunks_dir: Path, task_id: str, chunk_index: int) -> Path:
    return chunks_dir / task_id / f"{task_id}_chunk_{chunk_index:03d}.ogg"


async def handle_chunk_transcription(
    client: httpx.AsyncClient,
    transcription_limiter: TranscriptionRateLimiter,
    task_state: TaskState,
    status_dir: Path,
    results_dir: Path,
    chunks_dir: Path,
    total_chunks: int,
    ready_chunks: int,
    chunk_index: int,
) -> None:
    task_id = task_state.task_id
    logger = get_task_logger(task_id, chunk_index)
    chunk_path = get_chunk_path(chunks_dir, task_id, chunk_index)

    try:
        if not chunk_path.exists():
            raise FileNotFoundError(f"chunk file not found: {chunk_path}")

        write_status(status_dir, task_id, "transcribing", total_chunks, ready_chunks, error=None)
        text = await transcription_limiter.transcribe(client, task_id, chunk_path, chunk_index)
        task_state.transcriptions[chunk_index] = text
        write_transcription_cache(results_dir, task_id, task_state.transcriptions)
        logger.info("stored transcription in memory")
    except Exception as exc:
        logger.exception("transcription failed: %s", exc)
        write_status(
            status_dir,
            task_id,
            "api_error",
            total_chunks,
            ready_chunks,
            error=str(exc),
        )
        task_state.completed = True
    finally:
        task_state.in_progress_chunks.discard(chunk_index)


async def finalize_task(
    client: httpx.AsyncClient,
    llm_limiter: LlmRateLimiter,
    task_state: TaskState,
    status_dir: Path,
    results_dir: Path,
    total_chunks: int,
    ready_chunks: int,
    summary_chunk_size: int,
) -> None:
    task_id = task_state.task_id
    logger = get_task_logger(task_id)

    try:
        write_status(status_dir, task_id, "summarizing", total_chunks, ready_chunks, error=None)
        ordered_text = "\n\n".join(
            task_state.transcriptions[index] for index in sorted(task_state.transcriptions)
        )
        summary = await summarize_transcript(
            client,
            llm_limiter,
            task_id,
            ordered_text,
            summary_chunk_size,
        )

        write_status(status_dir, task_id, "generating_html", total_chunks, ready_chunks, error=None)
        await generate_html(client, llm_limiter, task_id, summary, results_dir)

        write_status(status_dir, task_id, "html_done", total_chunks, ready_chunks, error=None)
        task_state.completed = True
        logger.info("task completed")
    except Exception as exc:
        logger.exception("finalization failed: %s", exc)
        write_status(
            status_dir,
            task_id,
            "api_error",
            total_chunks,
            ready_chunks,
            error=str(exc),
        )
        task_state.completed = True
    finally:
        task_state.finalizing = False


async def process_tasks(
    client: httpx.AsyncClient,
    transcription_limiter: TranscriptionRateLimiter,
    llm_limiter: LlmRateLimiter,
    chunks_dir: Path,
    status_dir: Path,
    results_dir: Path,
    summary_chunk_size: int,
) -> None:
    active_tasks: dict[str, TaskState] = {}

    while True:
        for status_path in sorted(status_dir.glob("*.json")):
            task_id = status_path.stem
            logger = get_task_logger(task_id)

            try:
                status_payload = read_status(status_dir, task_id)
                status = status_payload.get("status")
                ready_chunks = int(status_payload.get("ready_chunks", 0))
                total_chunks = int(status_payload.get("total_chunks", 0))
            except Exception as exc:
                logger.exception("failed to read status file: %s", exc)
                continue

            if status not in {"ffmpeg_processing", "ffmpeg_done", "transcribing"}:
                if status != "api_error":
                    continue

            task_state = active_tasks.setdefault(task_id, TaskState(task_id=task_id))
            if not task_state.transcriptions:
                cached_transcriptions = read_transcription_cache(results_dir, task_id)
                if cached_transcriptions:
                    task_state.transcriptions.update(cached_transcriptions)
                    logger.info(
                        "loaded %s transcriptions from cache",
                        len(cached_transcriptions),
                    )

            if status not in {"ffmpeg_processing", "ffmpeg_done", "transcribing", "api_error"}:
                continue
            if task_state.completed:
                continue
            if status == "ffmpeg_done" or (
                status in {"transcribing", "api_error"} and total_chunks > 0 and ready_chunks >= total_chunks
            ):
                task_state.ffmpeg_done_seen = True

            for chunk_index in range(ready_chunks):
                if chunk_index in task_state.transcriptions or chunk_index in task_state.in_progress_chunks:
                    continue
                task_state.in_progress_chunks.add(chunk_index)
                logger.info("scheduling chunk %s for transcription", chunk_index, extra={"chunk_index": chunk_index})
                asyncio.create_task(
                    handle_chunk_transcription(
                        client,
                        transcription_limiter,
                        task_state,
                        status_dir,
                        results_dir,
                        chunks_dir,
                        total_chunks,
                        ready_chunks,
                        chunk_index,
                    )
                )

            all_done = total_chunks > 0 and len(task_state.transcriptions) == total_chunks
            no_pending = not task_state.in_progress_chunks
            if task_state.ffmpeg_done_seen and all_done and no_pending and not task_state.finalizing:
                if (results_dir / f"{task_id}.html").exists():
                    task_state.completed = True
                    logger.info("html already exists, skipping finalization")
                    continue
                task_state.finalizing = True
                await finalize_task(
                    client,
                    llm_limiter,
                    task_state,
                    status_dir,
                    results_dir,
                    total_chunks,
                    ready_chunks,
                    summary_chunk_size,
                )

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def main() -> None:
    load_dotenv()
    setup_logging()
    logger = get_task_logger("startup")

    chunks_dir = Path(os.getenv("CHUNKS_DIR", "shared-data/chunks"))
    status_dir = Path(os.getenv("STATUS_DIR", "shared-data/status"))
    results_dir = Path(os.getenv("RESULTS_DIR", "shared-data/results"))
    summary_chunk_size = int(os.getenv("SUMMARY_CHUNK_SIZE", "24000"))
    project_root = Path(__file__).resolve().parent.parent
    worker_prompts_dir = Path(__file__).resolve().parent / "prompts"
    prompt_sources = [
        Path(os.getenv("PROMPTS_SOURCE_DIR", "/external-prompts")),
        project_root / "prompts",
        Path(__file__).resolve().parent.parent / "prompts",
    ]

    ensure_directories(chunks_dir, status_dir, results_dir, worker_prompts_dir)
    copied_prompts, source_dir = sync_prompt_directories(prompt_sources, worker_prompts_dir)
    if source_dir is not None:
        logger.info("synced %s prompt files from %s into api-worker/prompts", copied_prompts, source_dir)
    else:
        logger.warning("no prompt source found, checked: %s", ", ".join(str(path) for path in prompt_sources))

    async with build_client() as client:
        transcription_limiter = TranscriptionRateLimiter()
        llm_limiter = LlmRateLimiter()
        await process_tasks(
            client,
            transcription_limiter,
            llm_limiter,
            chunks_dir,
            status_dir,
            results_dir,
            summary_chunk_size,
        )


if __name__ == "__main__":
    asyncio.run(main())
