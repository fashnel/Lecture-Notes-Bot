"""
Главный модуль воркера.

Запускает мониторинг директории /data/incoming через watchdog.
При появлении .webm файла — ставит в очередь на обработку.
Одновременно обрабатывается только один файл.
"""

import logging
import sys
import threading
import time
from pathlib import Path
from queue import Queue

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from config import Settings
from pipeline import LecturePipeline

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("worker")


class IncomingHandler(FileSystemEventHandler):
    """Обработчик событий файловой системы для входящей директории."""

    def __init__(self, queue: Queue):
        self.queue = queue
        self._processing = set()  # Уже поставленные в очередь файлы

    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix.lower() != ".webm":
            logger.debug("Пропущен файл (не .webm): %s", path.name)
            return

        # Избегаем дубликатов
        if path.name in self._processing:
            logger.warning("Файл уже в очереди: %s", path.name)
            return

        logger.info("Обнаружен новый файл: %s (%.1f MB)", path.name, path.stat().st_size / (1024 * 1024))
        self._processing.add(path.name)
        self.queue.put(path)

    def on_modified(self, event):
        """
        Иногда файлы копируются через on_modified (rsync, mv).
        Обрабатываем аналогично on_created.
        """
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix.lower() != ".webm":
            return

        if path.name in self._processing:
            return

        logger.info("Обнаружен файл (modified): %s", path.name)
        self._processing.add(path.name)
        self.queue.put(path)


def worker_loop(config: Settings, queue: Queue) -> None:
    """
    Основной рабочий цикл: берёт файлы из очереди и обрабатывает.
    Обрабатывает только один файл за раз.
    """
    pipeline = LecturePipeline(config)

    while True:
        video_path = queue.get()
        if video_path is None:  # Сигнал завершения
            queue.task_done()
            break

        logger.info("Начало обработки из очереди: %s", video_path.name)
        start_time = time.time()

        try:
            # Ждём, чтобы файл полностью записался (на случай медленного копирования)
            time.sleep(2)

            result_path = pipeline.process(video_path)
            elapsed = time.time() - start_time
            logger.info(
                "Файл успешно обработан: %s -> %s (%.1f мин)",
                video_path.name,
                result_path.name,
                elapsed / 60,
            )
        except Exception as e:
            logger.error(
                "Ошибка при обработке %s: %s",
                video_path.name,
                e,
                exc_info=True,
            )
        finally:
            queue.task_done()


def main() -> None:
    """Точка входа."""
    logger.info("Запуск Lecture Notes Bot Worker...")

    # Загрузка настроек
    try:
        config = Settings()
    except Exception as e:
        logger.error("Ошибка загрузки настроек: %s", e)
        logger.error("Убедитесь, что .env файл существует и заполнен корректно")
        sys.exit(1)

    # Создание директорий
    config.ensure_directories()
    logger.info("Директории: incoming=%s, output=%s, temp=%s", config.incoming_dir, config.output_dir, config.temp_dir)

    # Очередь обработки (один файл за раз)
    processing_queue: Queue = Queue(maxsize=10)

    # Запуск рабочего потока
    worker_thread = threading.Thread(
        target=worker_loop,
        args=(config, processing_queue),
        daemon=True,
        name="pipeline-worker",
    )
    worker_thread.start()
    logger.info("Рабочий поток запущен")

    # Настройка watchdog
    event_handler = IncomingHandler(processing_queue)
    observer = Observer()
    observer.schedule(event_handler, str(config.incoming_dir), recursive=False)
    observer.start()
    logger.info(
        "Watchdog наблюдает за: %s (ожидание .webm файлов)",
        config.incoming_dir,
    )

    logger.info("Worker готов. Нажмите Ctrl+C для остановки.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения (Ctrl+C)...")
    finally:
        observer.stop()
        observer.join()
        processing_queue.put(None)  # Сигнал завершения рабочему потоку
        worker_thread.join(timeout=30)
        logger.info("Worker остановлен.")


if __name__ == "__main__":
    main()
