# Gemini CLI Context: Lecture Notes Bot

This project is a Python-based worker designed to automatically convert video lectures (specifically `.webm` files) into structured PDF notes. It leverages external APIs for transcription (e.g., Groq Whisper) and summarization (LLM), while using local tools like FFmpeg for audio processing and WeasyPrint for PDF generation.

## Project Overview

- **Purpose:** Automate the creation of lecture summaries from video recordings.
- **Main Technologies:**
    - **Python 3.11:** Core language.
    - **FFmpeg:** Audio extraction and compression.
    - **watchdog:** Monitoring incoming files.
    - **httpx & tenacity:** API communication with retry logic.
    - **Pydantic Settings:** Configuration management.
    - **WeasyPrint:** HTML to PDF conversion.
- **Workflow:**
    1.  **Monitor:** `main.py` watches the `/data/incoming` directory for new `.webm` files.
    2.  **Queue:** Files are added to a single-threaded queue to process them one by one.
    3.  **Process:** `pipeline.py` executes the following steps:
        -   Extract and compress audio to MP3.
        -   Transcribe audio via API.
        -   Generate a Markdown summary using an LLM API.
        -   Convert the Markdown/HTML to PDF.
    4.  **Checkpointing:** The pipeline saves intermediate results (MP3, TXT) in a `/data/temp` directory to allow resuming from failures.

## Key Files

- `main.py`: Entry point. Sets up the file observer and the worker thread loop.
- `pipeline.py`: Contains the `LecturePipeline` class, implementing the core processing logic.
- `config.py`: Defines the `Settings` class using Pydantic for environment variable validation.
- `requirements.txt`: List of Python dependencies.
- `Dockerfile`: Multi-stage build for a lightweight image containing FFmpeg and necessary system libraries.
- `docker-compose.yml`: Orchestrates the worker service and volume mounts for data persistence.
- `.env.example`: Template for required API keys and configuration.

## Building and Running

### Local Development
1.  Install dependencies: `pip install -r requirements.txt`
2.  Install FFmpeg on your system.
3.  Configure `.env` based on `.env.example`.
4.  Run: `python main.py`

### Docker (Recommended)
1.  Create and fill `.env`.
2.  Build and start: `docker compose up -d --build`

## Development Conventions

- **Logging:** Use the `logger` from `logging` (configured in `main.py`). Use `DEBUG` for detailed execution traces and `INFO` for general progress.
- **Error Handling:** Use `tenacity` for retrying API calls. Wrap pipeline steps in try-except blocks to ensure the worker continues even if one file fails.
- **Checkpointing:** Always check if intermediate files (e.g., `.mp3`, `.txt`) exist before re-processing.
- **Resource Management:** Ensure temporary files are handled correctly. Video files are deleted after audio extraction to save space.
