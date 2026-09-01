"""Execucao dos checks e gravacao da serie historica.

A sessao do banco nunca fica aberta durante a rede: o coletor le o que precisa,
fecha a sessao, dispara os checks em paralelo e so entao reabre para gravar. Isso
mantem a escrita em uma transacao curta, que e o que o SQLite prefere.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from netpulse import incidents
from netpulse.checks import get_runner
from netpulse.checks.base import CheckFn, CheckOutcome, CheckTarget
from netpulse.config import Mode, get_settings
from netpulse.db import session_scope
from netpulse.models import Asset, Check, CheckResult, CheckType, Status, utcnow

logger = logging.getLogger(__name__)

RunnerFactory = Callable[[CheckType], CheckFn]


@dataclass(frozen=True, slots=True)
class Job:
    """Um check pronto para rodar, ja desligado da sessao do banco."""

    check_id: int
    asset_id: int
    check_type: CheckType
    target: CheckTarget


@dataclass(frozen=True, slots=True)
class Collected:
    """Resultado ja gravado, em forma simples de consumir apos a sessao fechar."""

    check_id: int
    asset_id: int
    status: Status
    latency_ms: float | None
    error: str | None
    ts: datetime


def due_checks(session: Session, *, now: datetime | None = None) -> list[Job]:
    """Checks habilitados cujo intervalo ja venceu desde a ultima execucao."""
    now = now or utcnow()

    last_run: dict[int, datetime] = dict(
        session.exec(
            select(CheckResult.check_id, func.max(CheckResult.ts)).group_by(CheckResult.check_id)
        ).all()
    )

    rows = session.exec(
        select(Check, Asset).join(Asset, Asset.id == Check.asset_id).where(Check.enabled)
    ).all()

    jobs: list[Job] = []
    for check, asset in rows:
        if not asset.enabled:
            continue
        previous = last_run.get(check.id)
        if previous is not None and (now - previous).total_seconds() < check.interval_seconds:
            continue
        jobs.append(
            Job(
                check_id=check.id,
                asset_id=asset.id,
                check_type=check.type,
                target=CheckTarget(
                    address=asset.address,
                    params=dict(check.params or {}),
                    timeout=check.timeout_seconds,
                ),
            )
        )
    return jobs


class Collector:
    """Roda checks e persiste os resultados.

    `runner_factory` existe para o modo demo e para os testes trocarem a
    implementacao real por uma sintetica sem tocar em nada mais.
    """

    def __init__(
        self,
        engine=None,
        *,
        runner_factory: RunnerFactory | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.max_concurrency = max_concurrency or settings.max_concurrency

        if runner_factory is None:
            if settings.mode is Mode.DEMO:
                from netpulse.demo import demo_runner_for

                runner_factory = demo_runner_for
            else:
                runner_factory = get_runner
        self.runner_factory = runner_factory

    async def execute(self, job: Job) -> CheckOutcome:
        """Roda um check. Nenhuma excecao escapa: um check quebrado vira um
        resultado UNKNOWN, e nao a queda do ciclo de coleta inteiro."""
        try:
            runner = self.runner_factory(job.check_type)
        except ValueError as exc:
            return CheckOutcome.unknown(str(exc))

        try:
            return await runner(job.target)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # fronteira de isolamento do ciclo de coleta
            logger.exception("check %s falhou de forma inesperada", job.check_id)
            return CheckOutcome.unknown(f"erro inesperado no check: {exc}")

    async def run_jobs(self, jobs: Sequence[Job]) -> list[Collected]:
        """Executa os jobs com concorrencia limitada e grava os resultados."""
        if not jobs:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def guarded(job: Job) -> CheckOutcome:
            async with semaphore:
                return await self.execute(job)

        outcomes = await asyncio.gather(*(guarded(job) for job in jobs))

        ts = utcnow()
        collected: list[Collected] = []
        with session_scope(self.engine) as session:
            for job, outcome in zip(jobs, outcomes, strict=True):
                session.add(
                    CheckResult(
                        check_id=job.check_id,
                        ts=ts,
                        status=outcome.status,
                        latency_ms=outcome.latency_ms,
                        detail=dict(outcome.detail),
                        error=outcome.error,
                    )
                )
                collected.append(
                    Collected(
                        check_id=job.check_id,
                        asset_id=job.asset_id,
                        status=outcome.status,
                        latency_ms=outcome.latency_ms,
                        error=outcome.error,
                        ts=ts,
                    )
                )

            # Reavalia com os resultados desta rodada ja na sessao, na mesma
            # transacao: nao existe instante em que a serie historica diz que
            # caiu e o incidente ainda nao sabe.
            session.flush()
            try:
                incidents.evaluate(session, now=ts)
            except Exception:
                # Correlacao quebrada nao pode custar a coleta — o dado bruto ja
                # esta gravado e a proxima rodada reavalia do zero.
                logger.exception("falha ao avaliar incidentes; a coleta segue")

        return collected

    async def run_once(self, *, now: datetime | None = None) -> list[Collected]:
        """Um ciclo completo: seleciona o que venceu, roda e grava."""
        with session_scope(self.engine) as session:
            jobs = due_checks(session, now=now)
        return await self.run_jobs(jobs)
