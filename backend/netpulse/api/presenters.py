"""Traducao dos objetos do dominio para os contratos da API."""

from netpulse.api.schemas import AssetStatusRead, CheckRead, CheckResultRead, CheckStatusRead
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
