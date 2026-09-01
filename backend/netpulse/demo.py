"""Modo demo: um parque sintetico que nao toca em nenhuma rede.

Existe para que qualquer pessoa consiga clonar o projeto e ver o sistema vivo,
sem inventario, sem credencial e sem VPN. Todos os enderecos vem das faixas
reservadas para documentacao da RFC 5737 — nenhum deles roteia para lugar nenhum.

O roteiro de falhas e proposital, nao um defeito: a cada 10 minutos, por 3
minutos, a sub-rede da filial inteira cai de uma vez. E o caso que o motor de
correlacao precisa reconhecer — seis ativos caindo juntos sao um uplink com
problema, nao seis problemas.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from random import Random

from sqlmodel import Session, select

from netpulse.checks.base import CheckFn, CheckOutcome, CheckTarget
from netpulse.models import (
    Asset,
    AssetKind,
    Check,
    CheckResult,
    CheckType,
    Incident,
    IncidentMember,
    IncidentStatus,
    Severity,
    Status,
)

# Sub-rede que sofre a queda coletiva roteirizada.
OUTAGE_SUBNET_PREFIX = "198.51.100."
OUTAGE_PERIOD_MINUTES = 10
OUTAGE_DURATION_MINUTES = 3

# Ativo que oscila sem cair de vez — o tipo de falha que so aparece no historico.
FLAKY_ADDRESS = "192.0.2.31"
# Ativo cronicamente lento, para exercitar o estado "degradado".
SLOW_ADDRESS = "192.0.2.32"
# Servico cujo certificado esta perto do vencimento.
EXPIRING_CERT_ADDRESS = "203.0.113.21"
EXPIRING_CERT_DAYS = 9


DEMO_ASSETS: list[dict] = [
    # Matriz — 192.0.2.0/24
    {
        "name": "fw-matriz",
        "address": "192.0.2.1",
        "kind": AssetKind.FIREWALL,
        "location": "Matriz / Rack A",
        "tags": ["borda", "critico"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 443})],
    },
    {
        "name": "sw-core-matriz",
        "address": "192.0.2.2",
        "kind": AssetKind.SWITCH,
        "location": "Matriz / Rack A",
        "tags": ["core", "critico"],
        "checks": [(CheckType.PING, {}), (CheckType.SNMP, {"oid": "1.3.6.1.2.1.1.1.0"})],
    },
    {
        "name": "srv-ad-01",
        "address": "192.0.2.10",
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack B",
        "tags": ["ad", "critico"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 389})],
    },
    {
        "name": "srv-arquivos",
        "address": "192.0.2.11",
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack B",
        "tags": ["arquivos"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 445})],
    },
    {
        "name": "srv-backup",
        "address": "192.0.2.12",
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack B",
        "tags": ["backup"],
        "checks": [(CheckType.PING, {})],
    },
    {
        "name": "srv-erp",
        "address": "192.0.2.20",
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack C",
        "tags": ["erp", "critico"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 1433})],
    },
    {
        "name": "srv-monitoramento",
        "address": FLAKY_ADDRESS,
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack C",
        "tags": ["observabilidade"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 9090})],
    },
    {
        "name": "srv-relatorios",
        "address": SLOW_ADDRESS,
        "kind": AssetKind.SERVER,
        "location": "Matriz / Rack C",
        "tags": ["bi"],
        "checks": [(CheckType.PING, {"degraded_above_ms": 120})],
    },
    # Filial — 198.51.100.0/24 (sofre a queda coletiva)
    {
        "name": "sw-filial",
        "address": "198.51.100.1",
        "kind": AssetKind.SWITCH,
        "location": "Filial / Sala tecnica",
        "tags": ["acesso"],
        "checks": [(CheckType.PING, {}), (CheckType.SNMP, {"oid": "1.3.6.1.2.1.1.1.0"})],
    },
    {
        "name": "ap-filial-recepcao",
        "address": "198.51.100.5",
        "kind": AssetKind.OTHER,
        "location": "Filial / Recepcao",
        "tags": ["wifi"],
        "checks": [(CheckType.PING, {})],
    },
    {
        "name": "impressora-filial-01",
        "address": "198.51.100.20",
        "kind": AssetKind.PRINTER,
        "location": "Filial / Administrativo",
        "tags": ["impressao"],
        "checks": [(CheckType.PING, {}), (CheckType.SNMP, {"oid": "1.3.6.1.2.1.43.10.2.1.4.1.1"})],
    },
    {
        "name": "impressora-filial-02",
        "address": "198.51.100.21",
        "kind": AssetKind.PRINTER,
        "location": "Filial / Atendimento",
        "tags": ["impressao"],
        "checks": [(CheckType.PING, {})],
    },
    {
        "name": "nvr-filial",
        "address": "198.51.100.30",
        "kind": AssetKind.OTHER,
        "location": "Filial / Sala tecnica",
        "tags": ["cftv"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 554})],
    },
    {
        "name": "pc-recepcao-filial",
        "address": "198.51.100.40",
        "kind": AssetKind.WORKSTATION,
        "location": "Filial / Recepcao",
        "tags": ["estacao"],
        "checks": [(CheckType.PING, {})],
    },
    # DMZ / servicos publicados — 203.0.113.0/24
    {
        "name": "portal-institucional",
        "address": "203.0.113.10",
        "kind": AssetKind.SERVICE,
        "location": "DMZ",
        "tags": ["publico", "web"],
        "checks": [(CheckType.TCP, {"port": 443}), (CheckType.SSL, {"port": 443})],
    },
    {
        "name": "webmail",
        "address": "203.0.113.11",
        "kind": AssetKind.SERVICE,
        "location": "DMZ",
        "tags": ["publico", "web"],
        "checks": [(CheckType.TCP, {"port": 443}), (CheckType.SSL, {"port": 443})],
    },
    {
        "name": "vpn-gateway",
        "address": "203.0.113.12",
        "kind": AssetKind.SERVICE,
        "location": "DMZ",
        "tags": ["publico", "acesso remoto", "critico"],
        "checks": [(CheckType.TCP, {"port": 443}), (CheckType.SSL, {"port": 443})],
    },
    {
        "name": "api-integracoes",
        "address": EXPIRING_CERT_ADDRESS,
        "kind": AssetKind.SERVICE,
        "location": "DMZ",
        "tags": ["publico", "api"],
        "checks": [(CheckType.TCP, {"port": 443}), (CheckType.SSL, {"port": 443})],
    },
    {
        "name": "proxy-reverso",
        "address": "203.0.113.30",
        "kind": AssetKind.SERVICE,
        "location": "DMZ",
        "tags": ["web"],
        "checks": [(CheckType.PING, {}), (CheckType.TCP, {"port": 80})],
    },
    {
        "name": "rtr-operadora",
        "address": "203.0.113.1",
        "kind": AssetKind.ROUTER,
        "location": "Matriz / Rack A",
        "tags": ["wan", "critico"],
        "checks": [(CheckType.PING, {})],
    },
]


def is_outage_window(now: datetime | None = None) -> bool:
    """A filial esta dentro da janela de queda roteirizada?"""
    now = now or datetime.now(UTC)
    return (now.minute % OUTAGE_PERIOD_MINUTES) < OUTAGE_DURATION_MINUTES


def _rng(address: str, bucket: int) -> Random:
    """Aleatoriedade estavel: o mesmo endereco no mesmo minuto da o mesmo valor,
    entao a serie historica fica coerente em vez de virar ruido puro."""
    return Random(f"{address}:{bucket}")


def _baseline_latency(address: str) -> float:
    return 2 + Random(address).random() * 18


def synthesize(
    check_type: CheckType, target: CheckTarget, *, now: datetime | None = None
) -> CheckOutcome:
    """Produz um resultado sintetico plausivel para um alvo."""
    now = now or datetime.now(UTC)
    address = target.address
    rng = _rng(address, int(now.timestamp() // 60))

    if address.startswith(OUTAGE_SUBNET_PREFIX) and is_outage_window(now):
        return CheckOutcome.down(
            "host inalcancavel (queda simulada da filial)",
            simulated=True,
        )

    if address == FLAKY_ADDRESS and rng.random() < 0.15:
        return CheckOutcome.down("host inalcancavel (instabilidade simulada)", simulated=True)

    latency = _baseline_latency(address) + rng.random() * 6
    if address == SLOW_ADDRESS:
        latency += 130

    if check_type is CheckType.SSL:
        days_left = (
            EXPIRING_CERT_DAYS
            if address == EXPIRING_CERT_ADDRESS
            else 60 + int(Random(address).random() * 250)
        )
        expires_at = now + timedelta(days=days_left)
        detail = {
            "port": int(target.param("port", 443)),
            "expires_at": expires_at.isoformat(),
            "days_left": days_left,
            "issuer": "Autoridade Certificadora de Exemplo",
            "common_name": address,
            "simulated": True,
        }
        warn_days = int(target.param("warn_days", 21))
        if days_left <= warn_days:
            return CheckOutcome(
                status=Status.DEGRADED,
                latency_ms=latency,
                detail=detail,
                error=f"certificado vence em {days_left} dia(s)",
            )
        return CheckOutcome(status=Status.UP, latency_ms=latency, detail=detail)

    threshold = target.param("degraded_above_ms")
    status = Status.DEGRADED if threshold is not None and latency > float(threshold) else Status.UP

    detail: dict = {"simulated": True}
    if check_type is CheckType.TCP:
        detail["port"] = int(target.param("port", 0))
    elif check_type is CheckType.SNMP:
        detail["oid"] = target.param("oid", "1.3.6.1.2.1.1.1.0")
        detail["value"] = "Equipamento simulado NetPulse"

    return CheckOutcome(status=status, latency_ms=latency, detail=detail)


def demo_runner_for(check_type: CheckType) -> CheckFn:
    """Fabrica de runners usada pelo coletor quando NETPULSE_MODE=demo."""

    async def runner(target: CheckTarget) -> CheckOutcome:
        # Um respiro para o loop de eventos, imitando a espera de rede.
        await asyncio.sleep(0)
        return synthesize(check_type, target)

    return runner


def backfill_history(
    session: Session,
    *,
    hours: int = 24,
    now: datetime | None = None,
    replace: bool = False,
) -> int:
    """Preenche a serie historica para tras, como se a coleta ja rodasse ha `hours`.

    Sem isso o painel abre com um ponto por check: o grafico de latencia e a linha
    do tempo ficam ilegiveis justamente na primeira impressao do projeto.

    Nao ha nada de aleatorio aqui que nao seja reproduzivel: `synthesize` deriva o
    resultado de `endereco:minuto`, entao a serie gerada agora e a mesma que a
    coleta real teria produzido naquele minuto — inclusive as quedas roteirizadas
    da filial e a oscilacao do host instavel.

    Retorna quantos resultados foram inseridos.
    """
    now = (now or datetime.now(UTC)).replace(tzinfo=None)
    inicio = now - timedelta(hours=hours)
    inseridos = 0

    for check in session.exec(select(Check)).all():
        if check.id is None:
            continue

        if replace:
            antigos = session.exec(
                select(CheckResult).where(
                    CheckResult.check_id == check.id, CheckResult.ts >= inicio
                )
            ).all()
            for antigo in antigos:
                session.delete(antigo)

        asset = session.get(Asset, check.asset_id)
        if asset is None:
            continue

        target = CheckTarget(
            address=asset.address,
            params=dict(check.params),
            timeout=check.timeout_seconds,
        )
        passo = timedelta(seconds=check.interval_seconds)

        # Comeca no passado e caminha ate agora, respeitando o intervalo do check:
        # a densidade da serie fica igual a que a coleta real teria gerado.
        ts = inicio
        while ts < now:
            outcome = synthesize(check.type, target, now=ts.replace(tzinfo=UTC))
            session.add(
                CheckResult(
                    check_id=check.id,
                    ts=ts,
                    status=outcome.status,
                    latency_ms=outcome.latency_ms,
                    detail=dict(outcome.detail),
                    error=outcome.error,
                )
            )
            inseridos += 1
            ts += passo

    return inseridos


def seed_demo(session: Session, *, force: bool = False) -> int:
    """Cria o parque sintetico. Sem `force`, nao mexe num banco que ja tem ativos.

    Retorna quantos ativos foram criados.
    """
    existing = session.exec(select(Asset)).first()
    if existing is not None and not force:
        return 0

    known = {asset.name for asset in session.exec(select(Asset)).all()}
    created = 0

    for spec in DEMO_ASSETS:
        if spec["name"] in known:
            continue
        asset = Asset(
            name=spec["name"],
            address=spec["address"],
            kind=spec["kind"],
            location=spec.get("location"),
            tags=list(spec.get("tags", [])),
        )
        asset.fill_subnet()
        session.add(asset)
        session.flush()  # precisa do id para pendurar os checks

        for check_type, params in spec["checks"]:
            session.add(
                Check(
                    asset_id=asset.id,
                    type=check_type,
                    params=dict(params),
                    interval_seconds=60,
                )
            )
        created += 1

    return created


def seed_demo_incidents(session: Session, *, now: datetime | None = None) -> int:
    """Registra uma queda coletiva passada para a demo abrir com uma linha do tempo.

    O coletor continua sendo quem cria incidentes futuros. Este registro sintetico
    representa a ultima janela roteirizada completa e e idempotente.
    """
    existing = session.exec(select(Incident)).first()
    if existing is not None:
        return 0

    now = (now or datetime.now(UTC)).replace(tzinfo=None, second=0, microsecond=0)
    block_start = now - timedelta(minutes=now.minute % OUTAGE_PERIOD_MINUTES)
    if now < block_start + timedelta(minutes=OUTAGE_DURATION_MINUTES):
        block_start -= timedelta(minutes=OUTAGE_PERIOD_MINUTES)
    recovered_at = block_start + timedelta(minutes=OUTAGE_DURATION_MINUTES)

    branch_assets = session.exec(
        select(Asset).where(Asset.subnet == "198.51.100.0/24").order_by(Asset.name)
    ).all()
    if not branch_assets:
        return 0

    incident = Incident(
        title=f"Queda correlacionada em {len(branch_assets)} ativos — 198.51.100.0/24",
        status=IncidentStatus.RESOLVED,
        severity=Severity.CRITICAL,
        correlation_key="subnet:198.51.100.0/24",
        subnet="198.51.100.0/24",
        opened_at=block_start,
        resolved_at=recovered_at,
    )
    session.add(incident)
    session.flush()

    member_count = 0
    for asset in branch_assets:
        for check in asset.checks:
            session.add(
                IncidentMember(
                    incident_id=incident.id,
                    asset_id=asset.id,
                    check_id=check.id,
                    first_failure_at=block_start,
                    recovered_at=recovered_at,
                )
            )
            member_count += 1
    return member_count
