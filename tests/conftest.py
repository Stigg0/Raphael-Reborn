"""Shared pytest configuration and fixtures."""
import sys
from pathlib import Path

# Application code lives under src/ (the services run with PYTHONPATH=/app/src);
# scripts/ live at the repo root. Put both on sys.path so tests import the exact
# same modules the running services do.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that call external APIs (Groq)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that call external APIs")
    if config.getoption("--run-integration"):
        import os
        os.environ["RUN_INTEGRATION_TESTS"] = "1"
