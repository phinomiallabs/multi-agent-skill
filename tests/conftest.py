"""Shared pytest fixtures for the monitor test suite.

Puts `monitor/` on the import path so tests can `import grok_tokens`,
`import generate_monitor`, etc., and exposes helpers for loading the JSON
fixtures under `tests/fixtures/`.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "monitor"))

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    """Load and parse a JSON fixture from tests/fixtures/."""
    return json.loads((FIXTURES / name).read_text())


def read_fixture_text(name: str) -> str:
    """Return the raw text of a fixture from tests/fixtures/."""
    return (FIXTURES / name).read_text()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
