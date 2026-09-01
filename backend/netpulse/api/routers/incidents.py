"""Leitura dos incidentes e parecer opcional solicitado pelo operador."""

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http
from sqlmodel import select

from netpulse import analysis as ai_analysis
from netpulse.api.deps import SessionDep
from netpulse.api.presenters import incident_status
from netpulse.api.schemas import IncidentRead
from netpulse.config import get_settings
from netpulse.models import Incident, IncidentStatus

router = APIRouter(prefix="/api/incidents", tags=["incidentes"])


@router.get("", response_model=list[IncidentRead], summary="Lista os incidentes")
def list_incidents(
    session: SessionDep,
    estado: IncidentStatus | None = Query(default=None),
    limite: int = Query(default=100, ge=1, le=1000),
) -> list[IncidentRead]:
    statement = select(Incident).order_by(Incident.opened_at.desc()).limit(limite)
    if estado is not None:
        statement = statement.where(Incident.status == estado)
    return [incident_status(session, row) for row in session.exec(statement).all()]


@router.get("/{incident_id}", response_model=IncidentRead, summary="Detalha um incidente")
def get_incident(session: SessionDep, incident_id: int) -> IncidentRead:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, f"incidente {incident_id} nao encontrado")
    return incident_status(session, incident)


@router.post(
    "/{incident_id}/analysis",
    response_model=IncidentRead,
    summary="Gera um parecer opcional por IA",
)
def analyze_incident(session: SessionDep, incident_id: int) -> IncidentRead:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, f"incidente {incident_id} nao encontrado")

    settings = get_settings()
    if not settings.ai_enabled:
        raise HTTPException(
            http.HTTP_503_SERVICE_UNAVAILABLE,
            "analise por IA desabilitada; configure ANTHROPIC_API_KEY para habilitar",
        )
    try:
        ai_analysis.generate_analysis(session, incident, settings)
    except ai_analysis.AnalysisUnavailable as exc:
        raise HTTPException(http.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    session.commit()
    session.refresh(incident)
    return incident_status(session, incident)
