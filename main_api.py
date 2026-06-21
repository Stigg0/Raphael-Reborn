"""API service entrypoint."""
import logging

import uvicorn

from api.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = create_app()

if __name__ == "__main__":
    uvicorn.run("main_api:app", host="0.0.0.0", port=8080, log_level="info")
