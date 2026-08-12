"""Consultas de leitura compartilhadas entre a API e a CLI.

O painel precisa do *ultimo* resultado de cada check, e um `SELECT ... LIMIT 1`
por check transformaria uma tela em dezenas de consultas. Aqui isso vira uma
consulta so, com funcao de janela, e o resultado e reaproveitado por quem
precisar.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from netpulse.models import Asset, Check, CheckResult, Status, utcnow, worst_status


def latest_results(session: Session) -> dict[int, CheckResult]:
    """Ultimo resultado de cada check, indexado por `check_id`."""
    ranked = select(
        CheckResult,
        func.row_number()
        .over(
            partition_by=CheckResult.check_id,
            order_by=(CheckResult.ts.desc(), CheckResult.id.desc()),
        )
        .label("rn"),
    ).subquery()
    latest = aliased(CheckResult, ranked)
    rows = session.exec(select(latest).where(ranked.c.rn == 1)).all()
    return {row.check_id: row for row in rows}


@dataclass(slots=True)
class AssetSnapshot:
    """Um ativo com o estado consolidado dos seus checks."""

    asset: Asset
    checks: list[Check]
    results: dict[int, CheckResult]

    @property
    def status(self) -> Status:
        return worst_status(result.status for result in self.results.values())

    @property
    def latency_ms(self) -> float | None:
        """Menor latencia observada entre os checks — a medida mais proxima do
        tempo de resposta do proprio ativo."""
        valores = [r.latency_ms for r in self.results.values() if r.latency_ms is not None]
        return min(valores) if valores else None

    @property
    def last_seen(self) -> datetime | None:
        if not self.results:
            return None
        return max(result.ts for result in self.results.values())

    @property
    def problems(self) -> list[str]:
        return [r.error for r in self.results.values() if r.status.is_failure and r.error]


def asset_snapshots(
    session: Session, *, assets: Iterable[Asset] | None = None
) -> list[AssetSnapshot]:
    """Estado atual de cada ativo, em duas consultas no total."""
    if assets is None:
        assets = session.exec(select(Asset).order_by(Asset.name)).all()
    else:
        assets = list(assets)

    results = latest_results(session)

    snapshots: list[AssetSnapshot] = []
    for asset in assets:
        checks = list(asset.checks)
        snapshots.append(
            AssetSnapshot(
                asset=asset,
                checks=checks,
                results={c.id: results[c.id] for c in checks if c.id in results},
            )
        )
    return snapshots


def status_counts(snapshots: Iterable[AssetSnapshot]) -> dict[Status, int]:
    """Quantos ativos em cada estado. Todos os estados aparecem, inclusive com
    zero — um painel que esconde a linha "down" quando esta tudo bem muda de
    forma a cada atualizacao."""
    counts = dict.fromkeys(Status, 0)
    for snapshot in snapshots:
        counts[snapshot.status] += 1
    return counts


def result_history(
    session: Session,
    check_id: int,
    *,
    hours: int = 24,
    limit: int = 1000,
) -> list[CheckResult]:
    """Serie historica de um check, do mais antigo para o mais recente."""
    since = utcnow() - timedelta(hours=hours)
    rows = session.exec(
        select(CheckResult)
        .where(CheckResult.check_id == check_id, CheckResult.ts >= since)
        .order_by(CheckResult.ts.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))
