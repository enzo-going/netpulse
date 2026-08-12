"""Resumo do parque — a unica chamada que o painel precisa fazer para desenhar
a tela inicial."""

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from netpulse import __version__
from netpulse.api.deps import SessionDep
from netpulse.api.presenters import asset_status
from netpulse.api.schemas import HealthRead, OverviewRead
from netpulse.config import get_settings
from netpulse.models import Incident, IncidentStatus, utcnow
from netpulse.queries import asset_snapshots, status_counts

router = APIRouter(prefix="/api", tags=["visao geral"])


@router.get("/health", response_model=HealthRead, summary="Estado do servico")
def health() -> HealthRead:
    settings = get_settings()
    return HealthRead(
        status="ok",
        version=__version__,
        mode=settings.mode.value,
        ai_enabled=settings.ai_enabled,
    )


@router.get("/overview", response_model=OverviewRead, summary="Resumo do parque")
def overview(session: SessionDep) -> OverviewRead:
    snapshots = asset_snapshots(session)
    counts = status_counts(snapshots)

    abertos = session.exec(
        select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.OPEN)
    ).one()

    # Ordenado do pior para o melhor: o painel mostra o que precisa de atencao no topo.
    problemas = sorted(
        (s for s in snapshots if s.status.is_failure),
        key=lambda s: (-s.status.rank, s.asset.name),
    )

    return OverviewRead(
        generated_at=utcnow(),
        total_assets=len(snapshots),
        counts=counts,
        open_incidents=abertos,
        degraded_or_down=[asset_status(s) for s in problemas],
    )
