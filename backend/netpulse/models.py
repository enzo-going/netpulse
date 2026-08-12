"""Modelo de dados do NetPulse.

Convencao de tempo: todo instante e gravado em UTC *sem* fuso (naive). O SQLite
nao preserva o offset, entao carregar um valor com fuso devolveria um naive e
qualquer subtracao entre os dois estouraria em TypeError. Gravando sempre naive
o comportamento fica igual em todos os bancos; a serializacao para o front
acrescenta o "Z" na borda da API.
"""

# Sem `from __future__ import annotations` de proposito: com as anotacoes adiadas
# o SQLAlchemy nao consegue resolver os tipos dos Relationship declarados abaixo.

from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import IPv4Network, ip_address, ip_network

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    """Instante atual em UTC, sem fuso. Ver a nota no topo do modulo."""
    return datetime.now(UTC).replace(tzinfo=None)


class CheckType(StrEnum):
    PING = "ping"
    TCP = "tcp"
    SNMP = "snmp"
    SSL = "ssl"


class Status(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"

    @property
    def is_failure(self) -> bool:
        return self in (Status.DOWN, Status.DEGRADED)

    @property
    def rank(self) -> int:
        """Ordem de gravidade. Um ativo com varios checks assume o pior deles:
        no painel, um host que responde ao ping mas perdeu a porta do servico
        precisa aparecer como problema, nao como sucesso parcial."""
        return _STATUS_RANK[self]


_STATUS_RANK = {
    Status.UP: 0,
    Status.UNKNOWN: 1,
    Status.DEGRADED: 2,
    Status.DOWN: 3,
}


def worst_status(statuses) -> Status:
    """O pior estado de um conjunto. Sem nenhum resultado, o estado e UNKNOWN."""
    statuses = list(statuses)
    if not statuses:
        return Status.UNKNOWN
    return max(statuses, key=lambda s: s.rank)


class AssetKind(StrEnum):
    SERVER = "server"
    SWITCH = "switch"
    ROUTER = "router"
    FIREWALL = "firewall"
    PRINTER = "printer"
    WORKSTATION = "workstation"
    SERVICE = "service"
    OTHER = "other"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


def derive_subnet(address: str, prefix: int = 24) -> str | None:
    """Sub-rede /24 de um endereco IPv4, usada para correlacionar falhas.

    Retorna None para hostnames e para IPv6 — nesses casos a correlacao cai
    para o agrupamento por localizacao do ativo.
    """
    try:
        parsed = ip_address(address)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    network = ip_network(f"{address}/{prefix}", strict=False)
    return str(IPv4Network(network))


class Asset(SQLModel, table=True):
    """Um equipamento ou servico monitorado."""

    __tablename__ = "assets"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    address: str = Field(index=True, description="IP ou hostname")
    kind: AssetKind = Field(default=AssetKind.OTHER)
    subnet: str | None = Field(default=None, index=True)
    location: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)

    checks: list["Check"] = Relationship(
        back_populates="asset",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    def fill_subnet(self) -> None:
        """Preenche a sub-rede a partir do endereco, quando ainda nao informada."""
        if self.subnet is None:
            self.subnet = derive_subnet(self.address)


class Check(SQLModel, table=True):
    """Uma verificacao periodica sobre um ativo."""

    __tablename__ = "checks"

    id: int | None = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="assets.id", index=True, ondelete="CASCADE")
    type: CheckType
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    interval_seconds: int = Field(default=60, ge=5)
    timeout_seconds: float = Field(default=5.0, gt=0)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)

    asset: Asset = Relationship(back_populates="checks")
    results: list["CheckResult"] = Relationship(
        back_populates="check",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def label(self) -> str:
        if self.type is CheckType.TCP and "port" in self.params:
            return f"tcp/{self.params['port']}"
        return self.type.value


class CheckResult(SQLModel, table=True):
    """Resultado de uma execucao de check. E a serie historica do sistema."""

    __tablename__ = "check_results"
    __table_args__ = (Index("ix_check_results_check_ts", "check_id", "ts"),)

    id: int | None = Field(default=None, primary_key=True)
    check_id: int = Field(foreign_key="checks.id", index=True, ondelete="CASCADE")
    ts: datetime = Field(default_factory=utcnow)
    status: Status = Field(default=Status.UNKNOWN)
    latency_ms: float | None = Field(default=None)
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = Field(default=None)

    check: Check = Relationship(back_populates="results")


class Incident(SQLModel, table=True):
    """Uma falha aberta. Falhas simultaneas na mesma sub-rede compartilham um
    incidente, para nao gerar um alerta por host quando o problema e o uplink."""

    __tablename__ = "incidents"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    status: IncidentStatus = Field(default=IncidentStatus.OPEN, index=True)
    severity: Severity = Field(default=Severity.WARNING)
    correlation_key: str = Field(index=True)
    subnet: str | None = Field(default=None)
    opened_at: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: datetime | None = Field(default=None)

    # Preenchido pelo analisador de IA. Opcional por design.
    analysis: str | None = Field(default=None)
    analysis_model: str | None = Field(default=None)
    analysis_at: datetime | None = Field(default=None)

    members: list["IncidentMember"] = Relationship(
        back_populates="incident",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class IncidentMember(SQLModel, table=True):
    """Ligacao entre um incidente e cada check que falhou dentro dele."""

    __tablename__ = "incident_members"
    __table_args__ = (Index("ix_incident_members_incident_check", "incident_id", "check_id"),)

    id: int | None = Field(default=None, primary_key=True)
    incident_id: int = Field(foreign_key="incidents.id", index=True, ondelete="CASCADE")
    asset_id: int = Field(foreign_key="assets.id", index=True)
    check_id: int = Field(foreign_key="checks.id", index=True)
    first_failure_at: datetime = Field(default_factory=utcnow)
    recovered_at: datetime | None = Field(default=None)

    incident: Incident = Relationship(back_populates="members")
