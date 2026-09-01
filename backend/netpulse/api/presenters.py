"""Traducao dos objetos do dominio para os contratos da API."""

from sqlmodel import Session, select

from netpulse.api.schemas import (
    AssetStatusRead,
    CheckRead,
    CheckResultRead,
    CheckStatusRead,
    IncidentMemberRead,
    IncidentRead,
)
from netpulse.models import Asset, Check, Incident, IncidentMember
from netpulse.queries import AssetSnapshot


def asset_status(snapshot: AssetSnapshot) -> AssetStatusRead:
    return AssetStatusRead(
        **snapshot.asset.model_dump(),
        status=snapshot.status,
        latency_ms=snapshot.latency_ms,
        last_seen=snapshot.last_seen,
        checks=[
            CheckStatusRead(
                check=CheckRead.model_validate(check),
                latest=(
                    CheckResultRead.model_validate(snapshot.results[check.id])
                    if check.id in snapshot.results
                    else None
                ),
            )
            for check in snapshot.checks
        ],
    )


def incident_status(session: Session, incident: Incident) -> IncidentRead:
    """Traduz membros com nomes legiveis sem expor detalhes internos do banco."""
    members = session.exec(
        select(IncidentMember)
        .where(IncidentMember.incident_id == incident.id)
        .order_by(IncidentMember.first_failure_at, IncidentMember.id)
    ).all()
    asset_ids = {member.asset_id for member in members}
    check_ids = {member.check_id for member in members}
    assets = {
        asset.id: asset
        for asset in session.exec(select(Asset).where(Asset.id.in_(asset_ids))).all()  # type: ignore[union-attr]
    }
    checks = {
        check.id: check
        for check in session.exec(select(Check).where(Check.id.in_(check_ids))).all()  # type: ignore[union-attr]
    }

    return IncidentRead(
        **incident.model_dump(exclude={"members"}),
        members=[
            IncidentMemberRead(
                asset_id=member.asset_id,
                asset_name=assets[member.asset_id].name,
                check_id=member.check_id,
                check_label=checks[member.check_id].label,
                first_failure_at=member.first_failure_at,
                recovered_at=member.recovered_at,
            )
            for member in members
            if member.asset_id in assets and member.check_id in checks
        ],
    )
