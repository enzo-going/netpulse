"""Criacao do engine e das sessoes.

O banco padrao e um SQLite em modo WAL: leitor e escritor nao se bloqueiam, o que
basta para o coletor gravar enquanto a API le, sem exigir nenhum servico externo.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from netpulse import models as _models  # noqa: F401  (registra as tabelas no metadata)
from netpulse.config import get_settings

_engine = None


def _is_memory_url(url: str) -> bool:
    return ":memory:" in url or url.rstrip("/").endswith("sqlite:")


def _ensure_parent_dir(url: str) -> None:
    """Cria o diretorio do arquivo .db, se o caminho apontar para disco."""
    prefix = "sqlite:///"
    if not url.startswith(prefix) or _is_memory_url(url):
        return
    path = Path(url[len(prefix) :])
    path.parent.mkdir(parents=True, exist_ok=True)


def _tune_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def make_engine(url: str | None = None, *, echo: bool = False):
    """Monta um engine novo. Util nos testes, que usam um banco em memoria."""
    url = url or get_settings().database_url
    kwargs: dict = {"echo": echo}

    if url.startswith("sqlite"):
        _ensure_parent_dir(url)
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_url(url):
            # Sem StaticPool cada conexao abriria um banco em memoria diferente.
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _tune_sqlite)
    return engine


def get_engine():
    """Engine compartilhado do processo."""
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def init_db(engine=None) -> None:
    SQLModel.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope(engine=None) -> Iterator[Session]:
    """Sessao transacional: comita no fim, desfaz em caso de erro."""
    with Session(engine or get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    """Dependencia do FastAPI."""
    with Session(get_engine()) as session:
        yield session
