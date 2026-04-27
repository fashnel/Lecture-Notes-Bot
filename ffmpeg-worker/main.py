import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from processor import process_file
from utils import ensure_directories, setup_logging


SCAN_INTERVAL_SECONDS = 2


async def scan_loop(input_dir: Path, chunks_dir: Path, status_dir: Path) -> None:
    logger = logging.getLogger("ffmpeg-worker.main")
    active_tasks: set[asyncio.Task] = set()

    while True:
        for file_path in sorted(input_dir.glob("*.webm")):
            if file_path.name.startswith("processing_"):
                continue

            task_id = file_path.stem
            processing_path = file_path.with_name(f"processing_{file_path.name}")

            try:
                file_path.rename(processing_path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning(
                    "failed to mark task %s as processing for file %s: %s",
                    task_id,
                    file_path.name,
                    exc,
                )
                continue

            task = asyncio.create_task(process_file(processing_path, chunks_dir, status_dir))
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)
            logger.info("scheduled task %s", task_id)

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def main() -> None:
    load_dotenv()
    setup_logging()

    input_dir = Path(os.getenv("INPUT_DIR", "shared-data/input"))
    chunks_dir = Path(os.getenv("CHUNKS_DIR", "shared-data/chunks"))
    status_dir = Path(os.getenv("STATUS_DIR", "shared-data/status"))

    ensure_directories(input_dir, chunks_dir, status_dir)
    await scan_loop(input_dir, chunks_dir, status_dir)


if __name__ == "__main__":
    asyncio.run(main())
