from __future__ import annotations

import pytest

from netpulse.config import get_settings
from netpulse.db import init_db, make_engine


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """Os testes nunca leem o .env do desenvolvedor nem tocam na rede."""
    monkeypatch.setenv("NETPULSE_MODE", "demo")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine():
    """Banco SQLite em memoria, recriado a cada teste."""
    eng = make_engine("sqlite://")
    init_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()
