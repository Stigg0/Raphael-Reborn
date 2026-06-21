"""Worker service entrypoint."""
import asyncio
import logging

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    settings = get_settings()

    from worker.consumer import run
    asyncio.run(run(settings))
