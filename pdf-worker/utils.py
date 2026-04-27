import json
import logging
import os
from pathlib import Path
from typing import Any


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
    logger = logging.getLogger("pdf-worker")
    return logging.LoggerAdapter(logger, {"task_id": task_id})


def ensure_directories(*paths: Path) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)


def read_status(status_dir: Path, task_id: str) -> dict[str, Any]:
    status_path = status_dir / f"{task_id}.json"
    return json.loads(status_path.read_text(encoding="utf-8"))


def write_status(
    status_dir: Path,
    task_id: str,
    status: str,
    error: str | None = None,
) -> None:
    status_path = status_dir / f"{task_id}.json"
    current_payload: dict[str, Any] = {}
    if status_path.exists():
        current_payload = json.loads(status_path.read_text(encoding="utf-8"))

    current_payload["status"] = status
    current_payload["error"] = error
    status_path.write_text(json.dumps(current_payload, ensure_ascii=False), encoding="utf-8")
