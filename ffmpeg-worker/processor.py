import asyncio
from pathlib import Path

from utils import ensure_directories, get_task_logger, write_status


CHUNK_SECONDS = 47 * 60

async def run_ffmpeg_command(task_id: str, args: list[str], chunk_index: int | None = None) -> None:
    logger = get_task_logger(task_id)
    if chunk_index is None:
        logger.info("running ffmpeg command: %s", " ".join(args))
    else:
        logger.info("running ffmpeg command for chunk %s: %s", chunk_index, " ".join(args))

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        message = stderr.decode().strip() or stdout.decode().strip() or "ffmpeg command failed"
        raise RuntimeError(message)


async def process_file(processing_file: Path, chunks_dir: Path, status_dir: Path) -> None:
    task_id = processing_file.name.removeprefix("processing_").removesuffix(".webm")
    logger = get_task_logger(task_id)
    task_chunk_dir = chunks_dir / task_id
    temp_dir = task_chunk_dir / "_tmp"

    try:
        logger.info("picked file %s", processing_file.name)
        ensure_directories(task_chunk_dir, temp_dir, status_dir)
        for stale_chunk in task_chunk_dir.glob(f"{task_id}_chunk_*.ogg"):
            stale_chunk.unlink(missing_ok=True)
        write_status(status_dir, task_id, "ffmpeg_processing", total_chunks=0, ready_chunks=0, error=None)

        segment_pattern = temp_dir / f"{task_id}_segment_%03d.webm"
        logger.info("segmenting source into 50-minute chunks")
        await run_ffmpeg_command(
            task_id,
            [
                "ffmpeg",
                "-y",
                "-i",
                str(processing_file),
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(CHUNK_SECONDS),
                str(segment_pattern),
            ],
        )

        segment_files = sorted(temp_dir.glob(f"{task_id}_segment_*.webm"))
        total_chunks = len(segment_files)
        logger.info("created %s temporary chunks", total_chunks)

        if total_chunks == 0:
            raise RuntimeError("ffmpeg produced no segments")

        ready_chunks = 0
        for chunk_index, segment_file in enumerate(segment_files):
            ogg_path = task_chunk_dir / f"{task_id}_chunk_{chunk_index:03d}.ogg"
            logger.info("compressing chunk %s to ogg", chunk_index)
            await run_ffmpeg_command(
                task_id,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(segment_file),
                    "-vn",
                    "-c:a",
                    "libopus",
                    "-q:a",
                    "0",
                    "-b:a",
                    "12k",
                    str(ogg_path),
                ],
                chunk_index=chunk_index,
            )
            ready_chunks += 1
            write_status(
                status_dir,
                task_id,
                "ffmpeg_processing",
                total_chunks=total_chunks,
                ready_chunks=ready_chunks,
                error=None,
            )
            logger.info("chunk %s ready (%s/%s)", chunk_index, ready_chunks, total_chunks)

        write_status(
            status_dir,
            task_id,
            "ffmpeg_done",
            total_chunks=total_chunks,
            ready_chunks=ready_chunks,
            error=None,
        )
        logger.info("processing completed")
    except Exception as exc:
        logger.exception("processing failed: %s", exc)
        write_status(
            status_dir,
            task_id,
            "ffmpeg_error",
            total_chunks=0,
            ready_chunks=0,
            error=str(exc),
        )
    finally:
        for temp_file in sorted(temp_dir.glob("*")) if temp_dir.exists() else []:
            temp_file.unlink(missing_ok=True)
        if temp_dir.exists():
            temp_dir.rmdir()
        processing_file.unlink(missing_ok=True)
        logger.info("cleaned temporary files")
