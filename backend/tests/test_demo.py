from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from netpulse.checks.base import CheckTarget
from netpulse.demo import (
    DEMO_ASSETS,
    EXPIRING_CERT_ADDRESS,
    SLOW_ADDRESS,
    demo_runner_for,
    is_outage_window,
    seed_demo,
    synthesize,
)
from netpulse.models import Asset, Check, CheckType, Status


def at(minute: int) -> datetime:
    return datetime(2026, 8, 6, 14, minute, 0, tzinfo=UTC)


class TestJanelaDeQueda:
    @pytest.mark.parametrize("minute", [0, 1, 2, 10, 11, 32])
    def test_dentro_da_janela(self, minute: int) -> None:
        assert is_outage_window(at(minute))

    @pytest.mark.parametrize("minute", [3, 5, 9, 15, 29])
    def test_fora_da_janela(self, minute: int) -> None:
        assert not is_outage_window(at(minute))


class TestSynthesize:
    def test_filial_cai_inteira_na_janela(self) -> None:
        for address in ("198.51.100.1", "198.51.100.20", "198.51.100.40"):
            outcome = synthesize(CheckType.PING, CheckTarget(address=address), now=at(1))
            assert outcome.status is Status.DOWN
            assert outcome.detail["simulated"] is True

    def test_filial_volta_fora_da_janela(self) -> None:
        outcome = synthesize(CheckType.PING, CheckTarget(address="198.51.100.1"), now=at(5))
        assert outcome.status is Status.UP

    def test_matriz_nao_e_afetada_pela_queda_da_filial(self) -> None:
        outcome = synthesize(CheckType.PING, CheckTarget(address="192.0.2.10"), now=at(1))
        assert outcome.status is Status.UP

    def test_resultado_e_estavel_dentro_do_mesmo_minuto(self) -> None:
        target = CheckTarget(address="192.0.2.10")
        primeiro = synthesize(CheckType.PING, target, now=at(7))
        segundo = synthesize(CheckType.PING, target, now=at(7))
        assert primeiro.latency_ms == segundo.latency_ms

    def test_host_lento_fica_degradado(self) -> None:
        target = CheckTarget(address=SLOW_ADDRESS, params={"degraded_above_ms": 120})
        outcome = synthesize(CheckType.PING, target, now=at(7))
        assert outcome.status is Status.DEGRADED
        assert outcome.latency_ms > 120

    def test_certificado_perto_do_vencimento_fica_degradado(self) -> None:
        target = CheckTarget(address=EXPIRING_CERT_ADDRESS, params={"port": 443})
        outcome = synthesize(CheckType.SSL, target, now=at(7))
        assert outcome.status is Status.DEGRADED
        assert outcome.detail["days_left"] <= 21
        assert "vence" in (outcome.error or "")

    def test_certificado_folgado_fica_up(self) -> None:
        target = CheckTarget(address="203.0.113.10", params={"port": 443})
        outcome = synthesize(CheckType.SSL, target, now=at(7))
        assert outcome.status is Status.UP
        assert outcome.detail["days_left"] > 21

    def test_snmp_devolve_um_valor(self) -> None:
        outcome = synthesize(CheckType.SNMP, CheckTarget(address="192.0.2.2"), now=at(7))
        assert outcome.status is Status.UP
        assert outcome.detail["value"]


async def test_demo_runner_e_assincrono() -> None:
    runner = demo_runner_for(CheckType.PING)
    outcome = await runner(CheckTarget(address="192.0.2.10"))
    assert outcome.status in (Status.UP, Status.DEGRADED, Status.DOWN)


class TestSeed:
    def test_cria_o_parque_completo(self, engine) -> None:
        with Session(engine) as session:
            criados = seed_demo(session)
            session.commit()

            assert criados == len(DEMO_ASSETS)
            assert len(session.exec(select(Asset)).all()) == len(DEMO_ASSETS)
            assert len(session.exec(select(Check)).all()) > len(DEMO_ASSETS)

    def test_preenche_a_sub_rede_dos_ativos(self, engine) -> None:
        with Session(engine) as session:
            seed_demo(session)
            session.commit()
            asset = session.exec(select(Asset).where(Asset.name == "sw-filial")).one()
            assert asset.subnet == "198.51.100.0/24"

    def test_nao_duplica_em_execucao_repetida(self, engine) -> None:
        with Session(engine) as session:
            seed_demo(session)
            session.commit()
            assert seed_demo(session) == 0
            session.commit()
            assert len(session.exec(select(Asset)).all()) == len(DEMO_ASSETS)

    def test_force_nao_recria_nomes_existentes(self, engine) -> None:
        with Session(engine) as session:
            seed_demo(session)
            session.commit()
            assert seed_demo(session, force=True) == 0
            session.commit()
            assert len(session.exec(select(Asset)).all()) == len(DEMO_ASSETS)


def test_todos_os_ativos_demo_usam_faixas_de_documentacao() -> None:
    """RFC 5737: nenhum endereco do modo demo pode rotear para uma rede real."""
    permitidos = ("192.0.2.", "198.51.100.", "203.0.113.")
    for spec in DEMO_ASSETS:
        assert spec["address"].startswith(permitidos), spec["name"]
