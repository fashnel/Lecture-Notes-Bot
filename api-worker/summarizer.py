import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from utils import clean_response, get_task_logger, load_prompt, request_with_retry


SUCCESS_DELAY_SECONDS = 61


class LlmRateLimiter:
    def __init__(self, delay_seconds: int = SUCCESS_DELAY_SECONDS) -> None:
        self.delay_seconds = delay_seconds
        self._lock = asyncio.Lock()
        self._last_success_at = 0.0

    async def call(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        system_prompt: str,
        user_text: str,
        chunk_index: int | None = None,
    ) -> str:
        async with self._lock:
            logger = get_task_logger(task_id, chunk_index)
            now = time.monotonic()
            elapsed = now - self._last_success_at
            wait_seconds = max(0.0, self.delay_seconds - elapsed)

            if self._last_success_at > 0 and wait_seconds > 0:
                logger.info("waiting %.1fs before next llm request", wait_seconds)
                await asyncio.sleep(wait_seconds)

            text = await call_llm(client, task_id, system_prompt, user_text)
            self._last_success_at = time.monotonic()
            return text


def split_text_for_summary(text: str, max_chars: int) -> list[str]:
    def split_long_text(value: str) -> list[str]:
        return [value[index:index + max_chars] for index in range(0, len(value), max_chars)]

    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_parts: list[str] = []
    current_length = 0

    for paragraph in paragraphs:
        part = paragraph.strip()
        if not part:
            continue

        if len(part) > max_chars:
            if current_parts:
                chunks.append("".join(current_parts))
                current_parts = []
                current_length = 0
            chunks.extend(split_long_text(part))
            continue

        addition = part if not current_parts else f"\n\n{part}"
        if current_parts and current_length + len(addition) > max_chars:
            chunks.append("".join(current_parts))
            current_parts = [part]
            current_length = len(part)
            continue

        current_parts.append(addition if current_parts else part)
        current_length += len(addition)

    if current_parts:
        chunks.append("".join(current_parts))

    return chunks


def extract_llm_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "".join(parts)

    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if isinstance(content_item, dict) and isinstance(content_item.get("text"), str):
                    parts.append(content_item["text"])
        if parts:
            return "".join(parts)

    raise RuntimeError("unable to extract text from LLM response")


async def call_llm(
    client: httpx.AsyncClient,
    task_id: str,
    system_prompt: str,
    user_text: str,
) -> str:
    llm_url = os.getenv("LLM_API_URL")
    llm_model = os.getenv("LLM_MODEL")
    llm_api_key = os.getenv("GROQ_API_KEY")

    if not llm_url:
        raise RuntimeError("LLM_API_URL is not set")
    if not llm_model:
        raise RuntimeError("LLM_MODEL is not set")
    if not llm_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")

    response = await request_with_retry(
        client,
        "POST",
        llm_url,
        task_id=task_id,
        rate_limit_header="x-ratelimit-reset-requests",
        headers={
            "Authorization": f"Bearer {llm_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        },
    )
    return extract_llm_text(response.json()).strip()


async def summarize_transcript(
    client: httpx.AsyncClient,
    llm_limiter: LlmRateLimiter,
    task_id: str,
    transcript_text: str,
    summary_chunk_size: int,
) -> str:
    logger = get_task_logger(task_id)
    prompt_name = os.getenv("LLM_SUMMARY_PROMPT")
    if not prompt_name:
        raise RuntimeError("LLM_SUMMARY_PROMPT is not set")

    system_prompt = load_prompt(prompt_name)
    text_chunks = split_text_for_summary(transcript_text, summary_chunk_size)
    logger.info("split transcript into %s summary chunks", len(text_chunks))

    semaphore = asyncio.Semaphore(3)

    async def summarize_one(index: int, chunk_text: str) -> tuple[int, str]:
        chunk_logger = get_task_logger(task_id, index)
        async with semaphore:
            chunk_logger.info("sending summary chunk to llm")
            result = await llm_limiter.call(
                client,
                task_id,
                system_prompt,
                chunk_text,
                chunk_index=index,
            )
            chunk_logger.info("summary chunk completed")
            return index, result

    tasks = [summarize_one(index, chunk_text) for index, chunk_text in enumerate(text_chunks)]
    summarized_parts = await asyncio.gather(*tasks)
    ordered_parts = [text for _, text in sorted(summarized_parts, key=lambda item: item[0])]
    return "\n\n".join(ordered_parts)


async def generate_html(
    client: httpx.AsyncClient,
    llm_limiter: LlmRateLimiter,
    task_id: str,
    summarized_text: str,
    results_dir: Path,
) -> Path:
    logger = get_task_logger(task_id)
    prompt_name = os.getenv("LLM_END_HTML_PROMPT")
    if not prompt_name:
        raise RuntimeError("LLM_END_HTML_PROMPT is not set")

    system_prompt = load_prompt(prompt_name)
    logger.info("requesting final html from llm")
    html = await llm_limiter.call(client, task_id, system_prompt, summarized_text)
    cleaned_html = clean_response(html)

    output_path = results_dir / f"{task_id}.html"
    output_path.write_text(cleaned_html, encoding="utf-8")
    logger.info("html saved to %s", output_path)
    return output_path
