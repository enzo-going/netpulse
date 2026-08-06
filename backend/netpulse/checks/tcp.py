"""Check de porta TCP: mede o tempo ate o handshake completar."""

from __future__ import annotations

import asyncio
import contextlib
from time import perf_counter

from netpulse.checks.base import CheckOutcome, CheckTarget, grade_latency, register
from netpulse.models import CheckType


async def _close(writer: asyncio.StreamWriter) -> None:
    # Fechar e melhor-esforco: o resultado da medicao ja foi obtido.
    writer.close()
    with contextlib.suppress(TimeoutError, OSError):
        await writer.wait_closed()


@register(CheckType.TCP)
async def check_tcp(target: CheckTarget) -> CheckOutcome:
    port = target.param("port")
    if port is None:
        return CheckOutcome.unknown("o check TCP exige o parametro `port`")

    started = perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target.address, int(port)),
            timeout=target.timeout,
        )
    except TimeoutError:
        return CheckOutcome.down(f"timeout ao conectar em {target.address}:{port}", port=int(port))
    except (OSError, ValueError) as exc:
        return CheckOutcome.down(f"conexao recusada em {port}: {exc}", port=int(port))

    latency = (perf_counter() - started) * 1000
    await _close(writer)

    return CheckOutcome(
        status=grade_latency(latency, target),
        latency_ms=latency,
        detail={"port": int(port)},
    )
