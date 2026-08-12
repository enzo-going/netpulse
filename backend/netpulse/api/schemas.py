"""Contratos de entrada e saida da API.

Os modelos de tabela nao sao usados como resposta de proposito: o formato que o
front consome nao deve mudar so porque uma coluna mudou de nome.

Sobre datas: o banco guarda UTC sem fuso (ver `netpulse.models`). A conversao para
ISO-8601 com "Z" acontece aqui, na borda — e o unico lugar que precisa saber
disso.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from netpulse.models import AssetKind, CheckType, IncidentStatus, Severity, Status


def _to_utc_iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_to_utc_iso, return_type=str)]

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=200)]


class CheckCreate(BaseModel):
    type: CheckType
    params: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = Field(default=60, ge=5, le=86_400)
    timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    enabled: bool = True

    @model_validator(mode="after")
    def _exige_porta_no_tcp(self) -> "CheckCreate":
        if self.type is CheckType.TCP and "port" not in self.params:
            raise ValueError("um check TCP exige o parametro `port`")
        return self


class CheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    type: CheckType
    label: str
    params: dict[str, Any]
    interval_seconds: int
    timeout_seconds: float
    enabled: bool


class CheckUpdate(BaseModel):
    params: dict[str, Any] | None = None
    interval_seconds: int | None = Field(default=None, ge=5, le=86_400)
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    enabled: bool | None = None


class AssetCreate(BaseModel):
    name: NonEmptyStr
    address: NonEmptyStr
    kind: AssetKind = AssetKind.OTHER
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    checks: list[CheckCreate] = Field(default_factory=list)


class AssetUpdate(BaseModel):
    name: NonEmptyStr | None = None
    address: NonEmptyStr | None = None
    kind: AssetKind | None = None
    location: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    kind: AssetKind
    subnet: str | None
    location: str | None
    tags: list[str]
    enabled: bool
    created_at: UtcDatetime


class CheckResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_id: int
    ts: UtcDatetime
    status: Status
    latency_ms: float | None
    detail: dict[str, Any]
    error: str | None


class CheckStatusRead(BaseModel):
    """Um check com o seu ultimo resultado, que e o que a grade do painel mostra."""

    check: CheckRead
    latest: CheckResultRead | None


class AssetStatusRead(AssetRead):
    """Ativo com o estado consolidado — o pior estado entre os seus checks."""

    status: Status
    latency_ms: float | None
    last_seen: UtcDatetime | None
    checks: list[CheckStatusRead]


class OverviewRead(BaseModel):
    generated_at: UtcDatetime
    total_assets: int
    counts: dict[Status, int]
    open_incidents: int
    degraded_or_down: list[AssetStatusRead]


class HistoryPoint(BaseModel):
    ts: UtcDatetime
    status: Status
    latency_ms: float | None


class CheckHistoryRead(BaseModel):
    check_id: int
    hours: int
    points: list[HistoryPoint]


class IncidentMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: int
    check_id: int
    first_failure_at: UtcDatetime
    recovered_at: UtcDatetime | None


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: IncidentStatus
    severity: Severity
    correlation_key: str
    subnet: str | None
    opened_at: UtcDatetime
    resolved_at: UtcDatetime | None
    analysis: str | None
    analysis_model: str | None
    analysis_at: UtcDatetime | None
    members: list[IncidentMemberRead] = Field(default_factory=list)


class HealthRead(BaseModel):
    status: str
    version: str
    mode: str
    ai_enabled: bool
