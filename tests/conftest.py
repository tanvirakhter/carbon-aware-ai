"""Shared test fixtures. All tests run fully offline against mock JSON."""

import json
import sys
from pathlib import Path

import pytest

# Make the src/ layout importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def national_json() -> dict:
    return _load("national_intensity.json")


@pytest.fixture
def factors_json() -> dict:
    return _load("factors.json")


@pytest.fixture
def regional_current_json() -> dict:
    return _load("regional_current.json")


@pytest.fixture
def regional_fw48h_json() -> dict:
    return _load("regional_fw48h.json")
