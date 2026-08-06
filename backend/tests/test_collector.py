from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlmodel import Session, select

from netpulse.checks.base import CheckFn, CheckOutcome, CheckTarget
from netpulse.collector import Collector, due_checks
from netpulse.models import Asset, Check, CheckResult, CheckType, Status, utcnow


def make_asset(session: Session, name: str, address: str, **check_kwargs) -> Check:
    asset = Asset(name=name, address=address)
    asset.fill_subnet()
    session.add(asset)
    session.flush()
    check = Check(asset_id=asset.id, type=check_kwargs.pop("type", CheckType.PING), **check_kwargs)
    session.add(check)
    session.flush()
    return check


def constant_factory(outcome: CheckOutcome):
    def factory(_check_type: CheckType) -> CheckFn:
        async def runner(_target: CheckTarget) -> CheckOutcome:
            return outcome

        return runner

    return factory


class TestDueChecks:
    def test_check_sem_historico_esta_vencido(self, engine) -> None:
        with Session(engine) as session:
            make_asset(session, "srv", "192.0.2.10", interval_seconds=60)
            session.commit()

            jobs = due_checks(session)
            assert len(jobs) == 1
            assert jobs[0].target.address == "192.0.2.10"

    def test_check_recem_executado_nao_esta_vencido(self, engine) -> None:
        with Session(engine) as session:
            check = make_asset(session, "srv", "192.0.2.10", interval_seconds=60)
            session.add(CheckResult(check_id=check.id, status=Status.UP, ts=utcnow()))
            session.commit()

            assert due_checks(session) == []

    def test_check_vence_apos_o_intervalo(self, engine) -> None:
        with Session(engine) as session:
            check = make_asset(session, "srv", "192.0.2.10", interval_seconds=60)
            antigo = utcnow() - timedelta(seconds=120)
            session.add(CheckResult(check_id=check.id, status=Status.UP, ts=antigo))
            session.commit()

            assert len(due_checks(session)) == 1

    def test_check_desabilitado_e_ignorado(self, engine) -> None:
        with Session(engine) as session:
            make_asset(session, "srv", "192.0.2.10", enabled=False)
            session.commit()
            assert due_checks(session) == []

    def test_ativo_desabilitado_e_ignorado(self, engine) -> None:
        with Session(engine) as session:
            check = make_asset(session, "srv", "192.0.2.10")
            asset = session.get(Asset, check.asset_id)
            asset.enabled = False
            session.commit()
            assert due_checks(session) == []

    def test_job_carrega_os_parametros_do_check(self, engine) -> None:
        with Session(engine) as session:
            make_asset(
                session,
                "portal",
                "203.0.113.10",
                type=CheckType.TCP,
                params={"port": 443},
                timeout_seconds=2.5,
            )
            session.commit()

            job = due_checks(session)[0]
            assert job.check_type is CheckType.TCP
            assert job.target.params == {"port": 443}
            assert job.target.timeout == 2.5


class TestCollector:
    async def test_grava_um_resultado_por_check(self, engine) -> None:
        with Session(engine) as session:
            make_asset(session, "a", "192.0.2.10")
            make_asset(session, "b", "192.0.2.11")
            session.commit()

        collector = Collector(
            engine,
            runner_factory=constant_factory(CheckOutcome(status=Status.UP, latency_ms=7.5)),
        )
        collected = await collector.run_once()

        assert len(collected) == 2
        assert {c.status for c in collected} == {Status.UP}

        with Session(engine) as session:
            resultados = session.exec(select(CheckResult)).all()
            assert len(resultados) == 2
            assert all(r.latency_ms == 7.5 for r in resultados)

    async def test_nao_repete_o_check_no_ciclo_seguinte(self, engine) -> None:
        with Session(engine) as session:
            make_asset(session, "a", "192.0.2.10", interval_seconds=3600)
            session.commit()

        collector = Collector(engine, runner_factory=constant_factory(CheckOutcome(Status.UP)))
        assert len(await collector.run_once()) == 1
        assert await collector.run_once() == []

    async def test_excecao_no_check_vira_resultado_unknown(self, engine) -> None:
        with Session(engine) as session:
            make_asset(session, "a", "192.0.2.10")
            session.commit()

        def explosive_factory(_check_type: CheckType) -> CheckFn:
            async def runner(_target: CheckTarget) -> CheckOutcome:
                raise RuntimeError("boom")

            return runner

        collector = Collector(engine, runner_factory=explosive_factory)
        collected = await collector.run_once()

        assert len(collected) == 1
        assert collected[0].status is Status.UNKNOWN
        assert "boom" in (collected[0].error or "")

    async def test_respeita_o_limite_de_concorrencia(self, engine) -> None:
        with Session(engine) as session:
            for i in range(10):
                make_asset(session, f"a{i}", f"192.0.2.{10 + i}")
            session.commit()

        em_voo = 0
        pico = 0

        def tracking_factory(_check_type: CheckType) -> CheckFn:
            async def runner(_target: CheckTarget) -> CheckOutcome:
                nonlocal em_voo, pico
                em_voo += 1
                pico = max(pico, em_voo)
                await asyncio.sleep(0.01)
                em_voo -= 1
                return CheckOutcome(status=Status.UP)

            return runner

        collector = Collector(engine, runner_factory=tracking_factory, max_concurrency=3)
        collected = await collector.run_once()

        assert len(collected) == 10
        assert pico <= 3

    async def test_ciclo_sem_nada_vencido_nao_grava(self, engine) -> None:
        collector = Collector(engine, runner_factory=constant_factory(CheckOutcome(Status.UP)))
        assert await collector.run_once() == []

        with Session(engine) as session:
            assert session.exec(select(CheckResult)).all() == []
