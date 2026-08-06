from __future__ import annotations

import asyncio

from sqlmodel import Session

from netpulse.checks.base import CheckFn, CheckOutcome, CheckTarget
from netpulse.collector import Collector
from netpulse.models import Asset, Check, CheckType, Status
from netpulse.scheduler import run_forever


def seed_one(session: Session, interval_seconds: int = 3600) -> None:
    asset = Asset(name="srv", address="192.0.2.10")
    asset.fill_subnet()
    session.add(asset)
    session.flush()
    session.add(Check(asset_id=asset.id, type=CheckType.PING, interval_seconds=interval_seconds))


def up_factory(_check_type: CheckType) -> CheckFn:
    async def runner(_target: CheckTarget) -> CheckOutcome:
        return CheckOutcome(status=Status.UP, latency_ms=1.0)

    return runner


async def test_para_no_numero_maximo_de_ciclos(engine) -> None:
    with Session(engine) as session:
        seed_one(session)
        session.commit()

    ciclos = await run_forever(
        Collector(engine, runner_factory=up_factory), tick_seconds=0, max_cycles=3
    )
    assert ciclos == 3


async def test_hook_recebe_os_resultados_do_ciclo(engine) -> None:
    with Session(engine) as session:
        seed_one(session)
        session.commit()

    vistos: list[int] = []

    async def hook(results) -> None:
        vistos.append(len(results))

    await run_forever(
        Collector(engine, runner_factory=up_factory),
        tick_seconds=0,
        max_cycles=2,
        on_cycle=hook,
    )
    # Primeiro ciclo coleta o check; o segundo nao, porque o intervalo nao venceu.
    assert vistos == [1, 0]


async def test_hook_sincrono_tambem_funciona(engine) -> None:
    with Session(engine) as session:
        seed_one(session)
        session.commit()

    chamadas = 0

    def hook(_results) -> None:
        nonlocal chamadas
        chamadas += 1

    await run_forever(
        Collector(engine, runner_factory=up_factory), tick_seconds=0, max_cycles=2, on_cycle=hook
    )
    assert chamadas == 2


async def test_evento_de_parada_encerra_o_laco(engine) -> None:
    with Session(engine) as session:
        seed_one(session)
        session.commit()

    stop = asyncio.Event()

    def hook(_results) -> None:
        stop.set()

    ciclos = await run_forever(
        Collector(engine, runner_factory=up_factory), tick_seconds=5, stop=stop, on_cycle=hook
    )
    assert ciclos == 1


async def test_falha_de_um_ciclo_nao_derruba_o_laco(engine) -> None:
    class BrokenCollector(Collector):
        def __init__(self) -> None:
            super().__init__(engine, runner_factory=up_factory)
            self.chamadas = 0

        async def run_once(self, *, now=None):
            self.chamadas += 1
            if self.chamadas == 1:
                raise RuntimeError("banco indisponivel")
            return []

    collector = BrokenCollector()
    ciclos = await run_forever(collector, tick_seconds=0, max_cycles=3)

    assert ciclos == 3
    assert collector.chamadas == 3
