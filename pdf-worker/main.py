import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from converter import convert_html_to_pdf
from utils import ensure_directories, get_task_logger, read_status, setup_logging, write_status


SCAN_INTERVAL_SECONDS = 2


async def process_task(task_id: str, status_dir: Path, results_dir: Path) -> None:
    logger = get_task_logger(task_id)
    html_path = results_dir / f"{task_id}.html"
    pdf_path = results_dir / f"{task_id}.pdf"

    try:
        logger.info("setting status to generating_pdf")
        write_status(status_dir, task_id, "generating_pdf", error=None)

        logger.info("reading html from %s", html_path)
        if not html_path.exists():
            raise FileNotFoundError(f"html file not found: {html_path}")

        logger.info("starting pdf conversion")
        await convert_html_to_pdf(html_path, pdf_path)

        logger.info("pdf written to %s", pdf_path)
        write_status(status_dir, task_id, "done", error=None)
        logger.info("task completed")
    except Exception as exc:
        logger.exception("pdf generation failed: %s", exc)
        write_status(status_dir, task_id, "pdf_error", error=str(exc))


async def scan_loop(status_dir: Path, results_dir: Path) -> None:
    logger = logging.getLogger("pdf-worker.main")
    active_tasks: dict[str, asyncio.Task] = {}

    while True:
        finished_task_ids = [task_id for task_id, task in active_tasks.items() if task.done()]
        for task_id in finished_task_ids:
            active_tasks.pop(task_id, None)

        for status_path in sorted(status_dir.glob("*.json")):
            task_id = status_path.stem
            task_logger = get_task_logger(task_id)

            if task_id in active_tasks:
                continue

            try:
                status_payload = read_status(status_dir, task_id)
            except Exception as exc:
                task_logger.exception("failed to read status file: %s", exc)
                continue

            if status_payload.get("status") != "html_done":
                continue

            logger.info("scheduling task %s", task_id, extra={"task_id": task_id})
            task = asyncio.create_task(process_task(task_id, status_dir, results_dir))
            active_tasks[task_id] = task

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def main() -> None:
    load_dotenv()
    setup_logging()

    results_dir = Path(os.getenv("RESULTS_DIR", "shared-data/results"))
    status_dir = Path(os.getenv("STATUS_DIR", "shared-data/status"))

    ensure_directories(results_dir, status_dir)
    logging.getLogger("pdf-worker.main").info(
        "starting pdf worker",
        extra={"task_id": "-"},
    )
    await scan_loop(status_dir, results_dir)


if __name__ == "__main__":
    asyncio.run(main())
