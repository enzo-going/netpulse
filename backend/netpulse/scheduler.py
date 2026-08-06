"""Laco de coleta.

Nao ha uma thread por check nem um cron por ativo: um unico laco acorda a cada
`tick`, pergunta ao coletor o que venceu e roda tudo que venceu de uma vez. Cada
check guarda o proprio intervalo, entao ativos criticos e perifericos convivem no
mesmo laco sem configuracao extra.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from netpulse.collector import Collected, Collector

logger = logging.getLogger(__name__)

CycleHook = Callable[[list[Collected]], Awaitable[None] | None]

DEFAULT_TICK_SECONDS = 5.0


async def _call_hook(hook: CycleHook | None, results: list[Collected]) -> None:
    if hook is None:
        return
    outcome = hook(results)
    if asyncio.iscoroutine(outcome):
        await outcome


async def run_forever(
    collector: Collector | None = None,
    *,
    tick_seconds: float = DEFAULT_TICK_SECONDS,
    stop: asyncio.Event | None = None,
    on_cycle: CycleHook | None = None,
    max_cycles: int | None = None,
) -> int:
    """Roda ate `stop` ser sinalizado (ou ate `max_cycles`, usado nos testes).

    Devolve quantos ciclos rodaram.
    """
    collector = collector or Collector()
    stop = stop or asyncio.Event()
    cycles = 0

    while not stop.is_set():
        try:
            results = await collector.run_once()
            if results:
                logger.info("ciclo concluido: %d check(s) executado(s)", len(results))
            await _call_hook(on_cycle, results)
        except asyncio.CancelledError:
            raise
        except Exception:  # um ciclo ruim nao derruba o laco
            logger.exception("o ciclo de coleta falhou; seguindo para o proximo")

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break

        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
        except TimeoutError:
            continue

    return cycles
