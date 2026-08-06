"""Endpoints de checks e da serie historica."""

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http

from netpulse.api.deps import SessionDep
from netpulse.api.schemas import CheckHistoryRead, CheckRead, CheckUpdate, HistoryPoint
from netpulse.models import Check
from netpulse.queries import result_history

router = APIRouter(prefix="/api/checks", tags=["checks"])


def _get_check(session: SessionDep, check_id: int) -> Check:
    check = session.get(Check, check_id)
    if check is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, f"check {check_id} nao encontrado")
    return check


@router.get("/{check_id}", response_model=CheckRead, summary="Detalha um check")
def get_check(session: SessionDep, check_id: int) -> CheckRead:
    return CheckRead.model_validate(_get_check(session, check_id))


@router.patch("/{check_id}", response_model=CheckRead, summary="Atualiza um check")
def update_check(session: SessionDep, check_id: int, payload: CheckUpdate) -> CheckRead:
    check = _get_check(session, check_id)
    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(check, campo, valor)
    session.commit()
    session.refresh(check)
    return CheckRead.model_validate(check)


@router.delete("/{check_id}", status_code=http.HTTP_204_NO_CONTENT, summary="Remove um check")
def delete_check(session: SessionDep, check_id: int) -> None:
    session.delete(_get_check(session, check_id))
    session.commit()


@router.get(
    "/{check_id}/history",
    response_model=CheckHistoryRead,
    summary="Serie historica de um check",
)
def get_history(
    session: SessionDep,
    check_id: int,
    horas: int = Query(default=24, ge=1, le=720, description="Janela em horas."),
    limite: int = Query(default=1000, ge=1, le=10_000),
) -> CheckHistoryRead:
    _get_check(session, check_id)
    resultados = result_history(session, check_id, hours=horas, limit=limite)
    return CheckHistoryRead(
        check_id=check_id,
        hours=horas,
        points=[
            HistoryPoint(ts=r.ts, status=r.status, latency_ms=r.latency_ms) for r in resultados
        ],
    )
