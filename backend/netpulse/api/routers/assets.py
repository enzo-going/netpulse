"""Endpoints de ativos."""

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from netpulse.api.deps import SessionDep
from netpulse.api.presenters import asset_status
from netpulse.api.schemas import (
    AssetCreate,
    AssetStatusRead,
    AssetUpdate,
    CheckCreate,
    CheckRead,
)
from netpulse.models import Asset, Check, Status
from netpulse.queries import asset_snapshots

router = APIRouter(prefix="/api/assets", tags=["ativos"])


def _get_asset(session: SessionDep, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, f"ativo {asset_id} nao encontrado")
    return asset


@router.get("", response_model=list[AssetStatusRead], summary="Lista os ativos e seu estado")
def list_assets(
    session: SessionDep,
    estado: Status | None = Query(default=None, description="Filtra pelo estado consolidado."),
    subnet: str | None = Query(default=None, description="Filtra pela sub-rede."),
    busca: str | None = Query(default=None, description="Trecho do nome ou do endereco."),
) -> list[AssetStatusRead]:
    statement = select(Asset).order_by(Asset.name)
    if subnet:
        statement = statement.where(Asset.subnet == subnet)
    if busca:
        padrao = f"%{busca}%"
        statement = statement.where(Asset.name.like(padrao) | Asset.address.like(padrao))

    snapshots = asset_snapshots(session, assets=session.exec(statement).all())
    if estado is not None:
        snapshots = [s for s in snapshots if s.status is estado]

    return [asset_status(snapshot) for snapshot in snapshots]


@router.post(
    "", response_model=AssetStatusRead, status_code=http.HTTP_201_CREATED, summary="Cria um ativo"
)
def create_asset(session: SessionDep, payload: AssetCreate) -> AssetStatusRead:
    asset = Asset(**payload.model_dump(exclude={"checks"}))
    asset.fill_subnet()
    session.add(asset)

    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            http.HTTP_409_CONFLICT, f"ja existe um ativo chamado {payload.name!r}"
        ) from None

    for check in payload.checks:
        session.add(Check(asset_id=asset.id, **check.model_dump()))

    session.commit()
    session.refresh(asset)
    return asset_status(asset_snapshots(session, assets=[asset])[0])


@router.get("/{asset_id}", response_model=AssetStatusRead, summary="Detalha um ativo")
def get_asset(session: SessionDep, asset_id: int) -> AssetStatusRead:
    asset = _get_asset(session, asset_id)
    return asset_status(asset_snapshots(session, assets=[asset])[0])


@router.patch("/{asset_id}", response_model=AssetStatusRead, summary="Atualiza um ativo")
def update_asset(session: SessionDep, asset_id: int, payload: AssetUpdate) -> AssetStatusRead:
    asset = _get_asset(session, asset_id)
    mudancas = payload.model_dump(exclude_unset=True)

    for campo, valor in mudancas.items():
        setattr(asset, campo, valor)

    # Trocar o endereco muda a sub-rede, que e o que agrupa os incidentes.
    if "address" in mudancas:
        asset.subnet = None
        asset.fill_subnet()

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(http.HTTP_409_CONFLICT, "ja existe um ativo com esse nome") from None

    session.refresh(asset)
    return asset_status(asset_snapshots(session, assets=[asset])[0])


@router.delete("/{asset_id}", status_code=http.HTTP_204_NO_CONTENT, summary="Remove um ativo")
def delete_asset(session: SessionDep, asset_id: int) -> None:
    session.delete(_get_asset(session, asset_id))
    session.commit()


@router.post(
    "/{asset_id}/checks",
    response_model=CheckRead,
    status_code=http.HTTP_201_CREATED,
    summary="Adiciona um check ao ativo",
)
def add_check(session: SessionDep, asset_id: int, payload: CheckCreate) -> CheckRead:
    _get_asset(session, asset_id)
    check = Check(asset_id=asset_id, **payload.model_dump())
    session.add(check)
    session.commit()
    session.refresh(check)
    return CheckRead.model_validate(check)
