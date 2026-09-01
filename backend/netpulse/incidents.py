"""Motor de incidentes: agrupa falhas simultaneas em vez de alertar por host.

A razao de existir do projeto esta aqui. Quando o uplink de uma filial cai, seis
ativos param de responder ao mesmo tempo — e isso e **um** problema, nao seis. Um
monitor que dispara seis alertas obriga o operador a descobrir sozinho que sao a
mesma coisa, justamente no momento em que ele tem menos tempo para isso.

Duas decisoes definem o comportamento:

`failure_threshold` — quantas coletas seguidas precisam falhar antes de virar
incidente. Um pacote perdido nao e uma queda; sem esse piso, todo soluco de rede
viraria alerta e o painel perderia credibilidade.

`correlation_window` — o quanto duas falhas podem estar separadas no tempo e
ainda serem tratadas como o mesmo evento. Ativos de uma mesma sub-rede raramente
caem no mesmo milissegundo: cada check tem seu proprio intervalo, entao a queda
coletiva chega espalhada por alguns segundos.

A chave de correlacao e a sub-rede quando o endereco e IPv4; para hostname e IPv6
cai para a localizacao do ativo, e na falta das duas o ativo responde por si —
sempre agrupando pelo maior escopo disponivel, nunca inventando um.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from netpulse.config import get_settings
from netpulse.models import (
    Asset,
    Check,
    CheckResult,
    Incident,
    IncidentMember,
    IncidentStatus,
    Severity,
    Status,
    utcnow,
)

FAILING = (Status.DOWN, Status.DEGRADED)


@dataclass(frozen=True, slots=True)
class CorrelationKey:
    """Como um grupo de falhas e identificado, e como ele se chama na tela."""

    value: str
    subnet: str | None
    label: str


def correlation_key_for(asset: Asset) -> CorrelationKey:
    """Maior escopo disponivel: sub-rede, senao localizacao, senao o proprio ativo."""
    if asset.subnet:
        return CorrelationKey(f"subnet:{asset.subnet}", asset.subnet, asset.subnet)
    if asset.location:
        return CorrelationKey(f"location:{asset.location}", None, asset.location)
    return CorrelationKey(f"asset:{asset.id}", None, asset.name)


def _consecutive_failures(session: Session, check_id: int, limit: int) -> list[CheckResult]:
    """Os ultimos resultados enquanto forem falha, do mais recente para tras.

    Para no primeiro sucesso: o que importa e a sequencia atual, nao o historico.
    """
    recentes = session.exec(
        select(CheckResult)
        .where(CheckResult.check_id == check_id)
        .order_by(CheckResult.ts.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()

    seguidas: list[CheckResult] = []
    for resultado in recentes:
        if resultado.status not in FAILING:
            break
        seguidas.append(resultado)
    return seguidas


def _severity_for(assets_afetados: int) -> Severity:
    """Um ativo isolado e um aviso; varios juntos e o caso que o projeto existe
    para reconhecer — provavelmente infraestrutura compartilhada."""
    return Severity.CRITICAL if assets_afetados > 1 else Severity.WARNING


def _title_for(chave: CorrelationKey, assets_afetados: int) -> str:
    if assets_afetados > 1:
        return f"{assets_afetados} ativos com falha em {chave.label}"
    return f"Falha em {chave.label}"


def evaluate(session: Session, *, now: datetime | None = None) -> list[Incident]:
    """Reavalia os incidentes a partir do estado atual dos checks.

    Idempotente: rodar duas vezes seguidas nao cria incidente duplicado nem
    reabre o que ja foi resolvido. O coletor chama isso ao fim de cada ciclo.

    Retorna os incidentes abertos ou atualizados nesta passagem.
    """
    settings = get_settings()
    agora = (now or utcnow()).replace(tzinfo=None)
    limite = settings.failure_threshold
    janela = timedelta(seconds=settings.correlation_window)

    checks = session.exec(select(Check).where(Check.enabled == True)).all()  # noqa: E712
    assets = {a.id: a for a in session.exec(select(Asset)).all()}

    # 1. Quem esta em falha confirmada agora — e desde quando.
    confirmados: dict[str, list[tuple[Check, datetime]]] = defaultdict(list)
    em_falha: set[int] = set()

    for check in checks:
        if check.id is None:
            continue
        asset = assets.get(check.asset_id)
        if asset is None or not asset.enabled:
            continue

        seguidas = _consecutive_failures(session, check.id, limite)
        if len(seguidas) < limite:
            continue

        em_falha.add(check.id)
        # A ultima da lista e a mais antiga da sequencia: quando a falha comecou.
        desde = seguidas[-1].ts
        confirmados[correlation_key_for(asset).value].append((check, desde))

    abertos = {
        inc.correlation_key: inc
        for inc in session.exec(
            select(Incident).where(Incident.status == IncidentStatus.OPEN)
        ).all()
    }

    tocados: list[Incident] = []

    # 2. Abre ou completa um incidente por chave de correlacao.
    for chave_valor, falhas in confirmados.items():
        primeiro_asset = assets[falhas[0][0].asset_id]
        chave = correlation_key_for(primeiro_asset)
        assets_afetados = len({c.asset_id for c, _ in falhas})

        incidente = abertos.get(chave_valor)
        if incidente is None:
            inicio = min(desde for _, desde in falhas)
            incidente = Incident(
                title=_title_for(chave, assets_afetados),
                status=IncidentStatus.OPEN,
                severity=_severity_for(assets_afetados),
                correlation_key=chave_valor,
                subnet=chave.subnet,
                opened_at=inicio,
            )
            session.add(incidente)
            session.flush()  # precisa do id para pendurar os membros
        else:
            # Uma queda que se espalha muda o titulo e a gravidade do incidente
            # que ja esta aberto, em vez de abrir um segundo.
            incidente.title = _title_for(chave, assets_afetados)
            incidente.severity = _severity_for(assets_afetados)

        existentes = {m.check_id: m for m in incidente.members}
        for check, desde in falhas:
            if check.id is None:
                continue
            membro = existentes.get(check.id)
            if membro is None:
                # So entra no incidente aberto se a falha comecou dentro da
                # janela; uma queda muito posterior e outro evento.
                if incidente.opened_at and desde - incidente.opened_at > janela:
                    continue
                session.add(
                    IncidentMember(
                        incident_id=incidente.id,
                        asset_id=check.asset_id,
                        check_id=check.id,
                        first_failure_at=desde,
                    )
                )
            elif membro.recovered_at is not None:
                # Voltou a falhar antes do incidente fechar: continua sendo o
                # mesmo problema, entao limpa a recuperacao em vez de duplicar.
                membro.recovered_at = None

        tocados.append(incidente)

    # 3. Marca recuperacao e fecha o que nao tem mais ninguem em falha.
    for chave_valor, incidente in abertos.items():
        for membro in incidente.members:
            if membro.check_id not in em_falha and membro.recovered_at is None:
                membro.recovered_at = agora

        if chave_valor not in confirmados:
            incidente.status = IncidentStatus.RESOLVED
            incidente.resolved_at = agora

    return tocados
