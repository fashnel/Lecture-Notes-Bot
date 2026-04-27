import json
import logging
from pathlib import Path
from typing import Optional


class TaskContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = "-"
        return True


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] [task_id=%(task_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(TaskContextFilter())


def get_task_logger(task_id: str) -> logging.LoggerAdapter:
    logger = logging.getLogger("ffmpeg-worker")
    return logging.LoggerAdapter(logger, {"task_id": task_id})


def ensure_directories(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def write_status(
    status_dir: Path,
    task_id: str,
    status: str,
    total_chunks: int,
    ready_chunks: int,
    error: Optional[str] = None,
) -> None:
    status_path = status_dir / f"{task_id}.json"
    payload = {
        "status": status,
        "total_chunks": total_chunks,
        "ready_chunks": ready_chunks,
        "error": error,
    }
    status_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
