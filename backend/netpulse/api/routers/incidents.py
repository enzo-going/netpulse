"""Endpoints de incidentes.

A leitura ja esta completa; quem cria e fecha incidente e o motor de correlacao,
que entra no proximo passo. Ate la a lista responde vazia — nao ha caminho falso
nem dado inventado.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http
from sqlmodel import select

from netpulse.api.deps import SessionDep
from netpulse.api.schemas import IncidentRead
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
    return [IncidentRead.model_validate(row) for row in session.exec(statement).all()]


@router.get("/{incident_id}", response_model=IncidentRead, summary="Detalha um incidente")
def get_incident(session: SessionDep, incident_id: int) -> IncidentRead:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, f"incidente {incident_id} nao encontrado")
    return IncidentRead.model_validate(incident)
