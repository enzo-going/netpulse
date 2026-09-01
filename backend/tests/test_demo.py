from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from netpulse.checks.base import CheckTarget
from netpulse.demo import (
    DEMO_ASSETS,
    EXPIRING_CERT_ADDRESS,
    SLOW_ADDRESS,
    backfill_history,
    demo_runner_for,
    is_outage_window,
    seed_demo,
    seed_demo_incidents,
    synthesize,
)
from netpulse.models import (
    Asset,
    Check,
    CheckResult,
    CheckType,
    Incident,
    IncidentMember,
    IncidentStatus,
    Status,
)


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


class TestBackfillHistory:
    """O historico sintetico existe para o painel abrir com grafico legivel."""

    def _parque(self, session: Session) -> None:
        seed_demo(session)
        session.commit()

    def test_gera_um_ponto_por_intervalo_de_cada_check(self, engine) -> None:
        agora = datetime(2026, 8, 6, 14, 0, 0, tzinfo=UTC)
        with Session(engine) as session:
            self._parque(session)
            checks = session.exec(select(Check)).all()
            esperado = sum(2 * 3600 // c.interval_seconds for c in checks)

            inseridos = backfill_history(session, hours=2, now=agora)
            session.commit()

            assert inseridos == esperado
            assert len(session.exec(select(CheckResult)).all()) == esperado

    def test_serie_cobre_a_janela_pedida_e_para_no_presente(self, engine) -> None:
        agora = datetime(2026, 8, 6, 14, 0, 0, tzinfo=UTC)
        with Session(engine) as session:
            self._parque(session)
            backfill_history(session, hours=6, now=agora)
            session.commit()

            marcas = sorted(r.ts for r in session.exec(select(CheckResult)).all())
            assert marcas[0] >= agora.replace(tzinfo=None) - timedelta(hours=6)
            assert marcas[-1] < agora.replace(tzinfo=None)

    def test_reproduz_a_queda_roteirizada_da_filial(self, engine) -> None:
        """A serie gerada precisa conter a queda coletiva, nao so ruido."""
        agora = datetime(2026, 8, 6, 14, 0, 0, tzinfo=UTC)
        with Session(engine) as session:
            self._parque(session)
            backfill_history(session, hours=2, now=agora)
            session.commit()

            filial = session.exec(
                select(Asset).where(Asset.address.startswith("198.51.100."))  # type: ignore[attr-defined]
            ).first()
            assert filial is not None
            checks = session.exec(select(Check).where(Check.asset_id == filial.id)).all()
            ids = [c.id for c in checks]
            pontos = session.exec(select(CheckResult).where(CheckResult.check_id.in_(ids))).all()  # type: ignore[attr-defined]

            derrubados = [p for p in pontos if p.status is Status.DOWN]
            assert derrubados, "a filial deveria cair dentro da janela roteirizada"
            assert len(derrubados) < len(pontos), "nao pode ficar derrubada o tempo todo"

    def test_e_deterministico(self, engine) -> None:
        """Mesma janela, mesmo resultado: a serie nao pode virar ruido aleatorio."""
        agora = datetime(2026, 8, 6, 14, 0, 0, tzinfo=UTC)
        with Session(engine) as session:
            self._parque(session)
            backfill_history(session, hours=1, now=agora)
            session.commit()
            primeira = [
                (r.check_id, r.ts, r.status) for r in session.exec(select(CheckResult)).all()
            ]

            backfill_history(session, hours=1, now=agora, replace=True)
            session.commit()
            segunda = [
                (r.check_id, r.ts, r.status) for r in session.exec(select(CheckResult)).all()
            ]

            assert sorted(primeira) == sorted(segunda)

    def test_refazer_nao_duplica_a_janela(self, engine) -> None:
        agora = datetime(2026, 8, 6, 14, 0, 0, tzinfo=UTC)
        with Session(engine) as session:
            self._parque(session)
            backfill_history(session, hours=1, now=agora)
            session.commit()
            antes = len(session.exec(select(CheckResult)).all())

            backfill_history(session, hours=1, now=agora, replace=True)
            session.commit()

            assert len(session.exec(select(CheckResult)).all()) == antes

    def test_horas_zero_nao_gera_nada(self, engine) -> None:
        with Session(engine) as session:
            self._parque(session)
            assert backfill_history(session, hours=0) == 0
            session.commit()
            assert session.exec(select(CheckResult)).all() == []


class TestDemoIncidents:
    def test_cria_uma_queda_coletiva_resolvida(self, engine) -> None:
        with Session(engine) as session:
            seed_demo(session)
            members = seed_demo_incidents(session, now=at(15))
            session.commit()

            incident = session.exec(select(Incident)).one()
            assert incident.status is IncidentStatus.RESOLVED
            assert incident.subnet == "198.51.100.0/24"
            assert "Queda correlacionada" in incident.title
            assert members == len(session.exec(select(IncidentMember)).all())
            assert members > 1

    def test_e_idempotente(self, engine) -> None:
        with Session(engine) as session:
            seed_demo(session)
            assert seed_demo_incidents(session, now=at(15)) > 0
            session.commit()
            assert seed_demo_incidents(session, now=at(15)) == 0


def test_tipos_de_check_tem_latencias_distintas() -> None:
    """Sem isso, dois checks do mesmo ativo desenham graficos sobrepostos no
    painel — o que parece defeito mesmo nao sendo."""
    alvo = CheckTarget(address="192.0.2.10", params={"port": 443})
    medidas = {
        tipo: synthesize(tipo, alvo, now=at(5)).latency_ms
        for tipo in (CheckType.PING, CheckType.TCP, CheckType.SSL, CheckType.SNMP)
    }

    assert len(set(medidas.values())) == len(medidas), "tipos diferentes, latencia igual"
    # A ordem imita a real: o ping so espera o eco; o TLS negocia certificado.
    assert medidas[CheckType.PING] < medidas[CheckType.TCP] < medidas[CheckType.SSL]


def test_latencia_continua_deterministica() -> None:
    """A serie historica reproduzivel depende disso."""
    alvo = CheckTarget(address="192.0.2.10")
    primeira = synthesize(CheckType.PING, alvo, now=at(5)).latency_ms
    segunda = synthesize(CheckType.PING, alvo, now=at(5)).latency_ms
    assert primeira == segunda


def test_todos_os_ativos_demo_usam_faixas_de_documentacao() -> None:
    """RFC 5737: nenhum endereco do modo demo pode rotear para uma rede real."""
    permitidos = ("192.0.2.", "198.51.100.", "203.0.113.")
    for spec in DEMO_ASSETS:
        assert spec["address"].startswith(permitidos), spec["name"]
