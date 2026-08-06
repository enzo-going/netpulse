"""Check de SNMP (GET em um OID).

O pysnmp e uma dependencia opcional (`pip install -e ".[snmp]"`). Sem ele o check
devolve UNKNOWN com a instrucao de instalacao, em vez de quebrar a coleta inteira
— um parque sem SNMP continua sendo monitorado por ping, TCP e TLS.

A assinatura de `UdpTransportTarget` mudou entre o pysnmp 6 e o 7, entao a
construcao do alvo trata os dois casos.
"""

from __future__ import annotations

import asyncio
import inspect
from time import perf_counter
from typing import Any

from netpulse.checks.base import CheckOutcome, CheckTarget, grade_latency, register
from netpulse.models import CheckType

DEFAULT_PORT = 161
DEFAULT_COMMUNITY = "public"
# sysDescr.0 — presente em praticamente todo agente SNMP.
DEFAULT_OID = "1.3.6.1.2.1.1.1.0"

_MISSING_DEP = 'suporte a SNMP nao instalado; rode `pip install -e ".[snmp]"` para habilitar'


async def _build_transport(udp_transport_target: Any, address: str, port: int, timeout: float):
    """Constroi o alvo UDP em qualquer uma das duas APIs do pysnmp."""
    factory = getattr(udp_transport_target, "create", None)
    if factory is not None:
        result = factory((address, port), timeout=int(timeout), retries=0)
        if inspect.isawaitable(result):
            return await result
        return result
    return udp_transport_target((address, port), timeout=int(timeout), retries=0)


@register(CheckType.SNMP)
async def check_snmp(target: CheckTarget) -> CheckOutcome:
    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            get_cmd,
        )
    except ImportError:
        return CheckOutcome.unknown(_MISSING_DEP)

    port = int(target.param("port", DEFAULT_PORT))
    community = target.param("community", DEFAULT_COMMUNITY)
    oid = str(target.param("oid", DEFAULT_OID))
    mp_model = 1 if str(target.param("version", "2c")) == "2c" else 0

    engine = SnmpEngine()
    started = perf_counter()
    try:
        transport = await _build_transport(UdpTransportTarget, target.address, port, target.timeout)
        error_indication, error_status, _, var_binds = await asyncio.wait_for(
            get_cmd(
                engine,
                CommunityData(community, mpModel=mp_model),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            ),
            timeout=target.timeout + 1,
        )
    except TimeoutError:
        return CheckOutcome.down(f"timeout no SNMP para {target.address}:{port}", oid=oid)
    except Exception as exc:  # noqa: BLE001 - a superficie de erro do pysnmp e ampla
        return CheckOutcome.down(f"falha no SNMP: {exc}", oid=oid)

    latency = (perf_counter() - started) * 1000

    if error_indication:
        return CheckOutcome.down(f"SNMP sem resposta: {error_indication}", oid=oid)
    if error_status:
        return CheckOutcome.down(f"SNMP retornou erro: {error_status.prettyPrint()}", oid=oid)

    value = str(var_binds[0][1]) if var_binds else None
    return CheckOutcome(
        status=grade_latency(latency, target),
        latency_ms=latency,
        detail={"oid": oid, "value": value, "port": port},
    )
