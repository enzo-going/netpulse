"""Correlacao deterministica e ciclo de vida dos incidentes.

Este modulo nao usa IA. Ele transforma resultados persistentes em incidentes,
agrupa quedas proximas e fecha cada incidente quando todos os checks envolvidos
se recuperam. A camada de IA apenas explica um incidente que ja existe.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlmodel import Session, select

from netpulse.models import (
    Asset,
    Check,
    CheckResult,
    Incident,
    IncidentMember,
    IncidentStatus,
    Severity,
    Status,
)


def _correlation_key(asset: Asset, check: Check, status: Status) -> str:
    """Quedas compartilham dominio de falha; degradacoes ficam no proprio check.

    Um certificado perto de vencer e um ping lento na mesma /24 nao indicam a
    queda do uplink. Somente DOWN ganha correlacao por sub-rede/localizacao.
    """
    if status is not Status.DOWN:
        return f"check:{check.id}"
    if asset.subnet:
        return f"subnet:{asset.subnet}"
    if asset.location:
        normalized = "-".join(asset.location.casefold().split())
        return f"location:{normalized}"
    return f"asset:{asset.id}"


def _failure_is_confirmed(
    session: Session,
    result: CheckResult,
    *,
    threshold: int,
) -> bool:
    recent = session.exec(
        select(CheckResult)
        .where(CheckResult.check_id == result.check_id, CheckResult.ts <= result.ts)
        .order_by(CheckResult.ts.desc(), CheckResult.id.desc())
        .limit(threshold)
    ).all()
    return len(recent) == threshold and all(item.status.is_failure for item in recent)


def _open_incident_for_member(
    session: Session,
    check_id: int,
) -> tuple[Incident, IncidentMember] | None:
    row = session.exec(
        select(Incident, IncidentMember)
        .join(IncidentMember, IncidentMember.incident_id == Incident.id)
        .where(
            Incident.status == IncidentStatus.OPEN,
            IncidentMember.check_id == check_id,
            IncidentMember.recovered_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(Incident.opened_at.desc())
    ).first()
    return row


def _recent_correlated_incident(
    session: Session,
    correlation_key: str,
    *,
    at: datetime,
    window_seconds: int,
) -> Incident | None:
    since = at - timedelta(seconds=window_seconds)
    return session.exec(
        select(Incident)
        .where(
            Incident.status == IncidentStatus.OPEN,
            Incident.correlation_key == correlation_key,
            Incident.opened_at >= since,
        )
        .order_by(Incident.opened_at.desc())
    ).first()


def _refresh_incident_summary(session: Session, incident: Incident) -> None:
    members = session.exec(
        select(IncidentMember).where(IncidentMember.incident_id == incident.id)
    ).all()
    asset_ids = {member.asset_id for member in members}
    assets = {
        asset.id: asset
        for asset in session.exec(select(Asset).where(Asset.id.in_(asset_ids))).all()  # type: ignore[union-attr]
    }
    names = sorted(assets[asset_id].name for asset_id in asset_ids if asset_id in assets)

    if incident.correlation_key.startswith("subnet:") and len(asset_ids) > 1:
        incident.title = f"Queda correlacionada em {len(asset_ids)} ativos — {incident.subnet}"
        incident.severity = Severity.CRITICAL
    elif len(names) == 1:
        incident.title = f"Falha confirmada em {names[0]}"
    else:
        incident.title = f"Falha confirmada em {len(asset_ids)} ativos"


def _recover_member(session: Session, result: CheckResult) -> list[Incident]:
    rows = session.exec(
        select(Incident, IncidentMember)
        .join(IncidentMember, IncidentMember.incident_id == Incident.id)
        .where(
            Incident.status == IncidentStatus.OPEN,
            IncidentMember.check_id == result.check_id,
            IncidentMember.recovered_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    touched: list[Incident] = []
    for incident, member in rows:
        member.recovered_at = result.ts
        touched.append(incident)
    session.flush()
    return touched


def _resolve_if_recovered(session: Session, incident: Incident, *, at: datetime) -> bool:
    active = session.exec(
        select(IncidentMember).where(
            IncidentMember.incident_id == incident.id,
            IncidentMember.recovered_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if active is not None:
        return False
    incident.status = IncidentStatus.RESOLVED
    incident.resolved_at = at
    return True


def process_results(
    session: Session,
    results: Sequence[CheckResult],
    *,
    failure_threshold: int,
    correlation_window: int,
) -> list[Incident]:
    """Aplica resultados recem-gravados aos incidentes e devolve os alterados."""
    if not results:
        return []

    session.flush()
    check_ids = {result.check_id for result in results}
    checks = {
        check.id: check
        for check in session.exec(select(Check).where(Check.id.in_(check_ids))).all()  # type: ignore[union-attr]
    }
    asset_ids = {check.asset_id for check in checks.values()}
    assets = {
        asset.id: asset
        for asset in session.exec(select(Asset).where(Asset.id.in_(asset_ids))).all()  # type: ignore[union-attr]
    }

    touched: dict[int, Incident] = {}
    for result in sorted(results, key=lambda item: (item.ts, item.check_id)):
        if result.status is Status.UP:
            for incident in _recover_member(session, result):
                _resolve_if_recovered(session, incident, at=result.ts)
                touched[incident.id] = incident
            continue

        if not result.status.is_failure:
            continue
        if not _failure_is_confirmed(session, result, threshold=failure_threshold):
            continue
        if _open_incident_for_member(session, result.check_id) is not None:
            continue

        check = checks.get(result.check_id)
        if check is None:
            continue
        asset = assets.get(check.asset_id)
        if asset is None:
            continue

        key = _correlation_key(asset, check, result.status)
        incident = _recent_correlated_incident(
            session,
            key,
            at=result.ts,
            window_seconds=correlation_window,
        )
        if incident is None:
            incident = Incident(
                title=f"Falha confirmada em {asset.name}",
                severity=(Severity.CRITICAL if result.status is Status.DOWN else Severity.WARNING),
                correlation_key=key,
                subnet=asset.subnet if key.startswith("subnet:") else None,
                opened_at=result.ts,
            )
            session.add(incident)
            session.flush()

        session.add(
            IncidentMember(
                incident_id=incident.id,
                asset_id=asset.id,
                check_id=check.id,
                first_failure_at=result.ts,
            )
        )
        session.flush()
        _refresh_incident_summary(session, incident)
        touched[incident.id] = incident

    return list(touched.values())
