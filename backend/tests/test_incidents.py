from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from netpulse.incidents import correlation_key_for, evaluate
from netpulse.models import (
    Asset,
    Check,
    CheckResult,
    CheckType,
    Incident,
    IncidentStatus,
    Severity,
    Status,
)

AGORA = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)


def _asset(session: Session, name: str, address: str, *, location: str | None = None) -> Asset:
    asset = Asset(name=name, address=address, location=location)
    asset.fill_subnet()
    session.add(asset)
    session.flush()
    return asset


def _check(session: Session, asset: Asset, tipo: CheckType = CheckType.PING) -> Check:
    check = Check(asset_id=asset.id, type=tipo, interval_seconds=60)
    session.add(check)
    session.flush()
    return check


def _resultados(
    session: Session,
    check: Check,
    status: list[Status],
    *,
    ate: datetime = AGORA,
) -> None:
    """Grava uma sequencia terminando em `ate`, um minuto entre cada."""
    base = ate.replace(tzinfo=None)
    total = len(status)
    for i, st in enumerate(status):
        session.add(
            CheckResult(
                check_id=check.id,
                ts=base - timedelta(minutes=(total - 1 - i)),
                status=st,
                latency_ms=None if st is Status.DOWN else 10.0,
            )
        )
    session.flush()


class TestChaveDeCorrelacao:
    def test_ipv4_agrupa_por_sub_rede(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "sw", "198.51.100.1", location="Filial")
            chave = correlation_key_for(asset)
            assert chave.value == "subnet:198.51.100.0/24"
            assert chave.subnet == "198.51.100.0/24"

    def test_hostname_cai_para_localizacao(self, engine) -> None:
        """derive_subnet devolve None fora de IPv4; o agrupamento nao pode sumir."""
        with Session(engine) as session:
            asset = _asset(session, "srv", "servidor.interno", location="Matriz")
            chave = correlation_key_for(asset)
            assert chave.value == "location:Matriz"
            assert chave.subnet is None

    def test_sem_sub_rede_nem_local_o_ativo_responde_por_si(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "solto", "maquina.local")
            assert correlation_key_for(asset).value == f"asset:{asset.id}"


class TestAberturaDeIncidente:
    def test_falha_isolada_nao_abre_antes_do_limite(self, engine) -> None:
        """Um pacote perdido nao e uma queda."""
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            _resultados(session, check, [Status.UP, Status.UP, Status.DOWN])

            assert evaluate(session, now=AGORA) == []
            session.commit()
            assert session.exec(select(Incident)).all() == []

    def test_abre_ao_atingir_o_limite(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            _resultados(session, check, [Status.DOWN, Status.DOWN, Status.DOWN])

            evaluate(session, now=AGORA)
            session.commit()

            incidente = session.exec(select(Incident)).one()
            assert incidente.status is IncidentStatus.OPEN
            assert incidente.severity is Severity.WARNING
            assert len(incidente.members) == 1

    def test_queda_coletiva_vira_um_incidente_so(self, engine) -> None:
        """O caso que o projeto existe para reconhecer: seis ativos da mesma
        sub-rede caindo juntos sao um uplink, nao seis problemas."""
        with Session(engine) as session:
            for i in range(1, 7):
                asset = _asset(session, f"filial-{i}", f"198.51.100.{i}")
                check = _check(session, asset)
                _resultados(session, check, [Status.DOWN] * 3)

            evaluate(session, now=AGORA)
            session.commit()

            incidentes = session.exec(select(Incident)).all()
            assert len(incidentes) == 1, "seis alertas em vez de um incidente"

            incidente = incidentes[0]
            assert incidente.subnet == "198.51.100.0/24"
            assert incidente.severity is Severity.CRITICAL
            assert len(incidente.members) == 6
            assert "6 ativos" in incidente.title

    def test_sub_redes_diferentes_nao_se_misturam(self, engine) -> None:
        with Session(engine) as session:
            for address in ("198.51.100.1", "192.0.2.1"):
                asset = _asset(session, f"host-{address}", address)
                _resultados(session, _check(session, asset), [Status.DOWN] * 3)

            evaluate(session, now=AGORA)
            session.commit()

            incidentes = session.exec(select(Incident)).all()
            assert len(incidentes) == 2
            assert {i.subnet for i in incidentes} == {"198.51.100.0/24", "192.0.2.0/24"}

    def test_degradado_tambem_conta_como_falha(self, engine) -> None:
        """Responder devagar e o aviso que antecede a queda; ignorar seria perder
        justamente o alerta util."""
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            _resultados(session, check, [Status.DEGRADED] * 3)

            evaluate(session, now=AGORA)
            session.commit()
            assert len(session.exec(select(Incident)).all()) == 1


class TestIdempotencia:
    def test_rodar_duas_vezes_nao_duplica(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            _resultados(session, _check(session, asset), [Status.DOWN] * 3)

            evaluate(session, now=AGORA)
            session.commit()
            evaluate(session, now=AGORA + timedelta(seconds=30))
            session.commit()

            incidentes = session.exec(select(Incident)).all()
            assert len(incidentes) == 1
            assert len(incidentes[0].members) == 1

    def test_ativo_novo_entra_no_incidente_aberto(self, engine) -> None:
        with Session(engine) as session:
            primeiro = _asset(session, "filial-1", "198.51.100.1")
            _resultados(session, _check(session, primeiro), [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            segundo = _asset(session, "filial-2", "198.51.100.2")
            _resultados(session, _check(session, segundo), [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            incidente = session.exec(select(Incident)).one()
            assert len(incidente.members) == 2
            assert incidente.severity is Severity.CRITICAL, "espalhou, entao escalou"


class TestResolucao:
    def test_fecha_quando_todos_voltam(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            _resultados(session, check, [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            depois = AGORA + timedelta(minutes=3)
            _resultados(session, check, [Status.UP] * 3, ate=depois)
            evaluate(session, now=depois)
            session.commit()

            incidente = session.exec(select(Incident)).one()
            assert incidente.status is IncidentStatus.RESOLVED
            assert incidente.resolved_at is not None
            assert incidente.members[0].recovered_at is not None

    def test_segue_aberto_enquanto_um_continua_caido(self, engine) -> None:
        with Session(engine) as session:
            a1 = _asset(session, "filial-1", "198.51.100.1")
            a2 = _asset(session, "filial-2", "198.51.100.2")
            c1, c2 = _check(session, a1), _check(session, a2)
            _resultados(session, c1, [Status.DOWN] * 3)
            _resultados(session, c2, [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            depois = AGORA + timedelta(minutes=3)
            _resultados(session, c1, [Status.UP] * 3, ate=depois)
            _resultados(session, c2, [Status.DOWN] * 3, ate=depois)
            evaluate(session, now=depois)
            session.commit()

            incidente = session.exec(select(Incident)).one()
            assert incidente.status is IncidentStatus.OPEN

            recuperados = {m.check_id: m.recovered_at for m in incidente.members}
            assert recuperados[c1.id] is not None
            assert recuperados[c2.id] is None

    def test_recaida_antes_de_fechar_nao_duplica(self, engine) -> None:
        """Voltou e caiu de novo dentro do mesmo incidente: continua sendo o
        mesmo problema."""
        with Session(engine) as session:
            a1 = _asset(session, "filial-1", "198.51.100.1")
            a2 = _asset(session, "filial-2", "198.51.100.2")
            c1, c2 = _check(session, a1), _check(session, a2)
            _resultados(session, c1, [Status.DOWN] * 3)
            _resultados(session, c2, [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            meio = AGORA + timedelta(minutes=3)
            _resultados(session, c1, [Status.UP] * 3, ate=meio)
            evaluate(session, now=meio)
            session.commit()

            fim = AGORA + timedelta(minutes=6)
            _resultados(session, c1, [Status.DOWN] * 3, ate=fim)
            evaluate(session, now=fim)
            session.commit()

            incidentes = session.exec(select(Incident)).all()
            assert len(incidentes) == 1
            membro = next(m for m in incidentes[0].members if m.check_id == c1.id)
            assert membro.recovered_at is None

    def test_incidente_resolvido_nao_reabre(self, engine) -> None:
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            _resultados(session, check, [Status.DOWN] * 3)
            evaluate(session, now=AGORA)
            session.commit()

            depois = AGORA + timedelta(minutes=3)
            _resultados(session, check, [Status.UP] * 3, ate=depois)
            evaluate(session, now=depois)
            session.commit()

            # Cai de novo mais tarde: e um evento novo, nao o antigo revivido.
            muito_depois = AGORA + timedelta(hours=2)
            _resultados(session, check, [Status.DOWN] * 3, ate=muito_depois)
            evaluate(session, now=muito_depois)
            session.commit()

            incidentes = session.exec(select(Incident).order_by(Incident.id)).all()  # type: ignore[arg-type]
            assert len(incidentes) == 2
            assert incidentes[0].status is IncidentStatus.RESOLVED
            assert incidentes[1].status is IncidentStatus.OPEN


class TestAtivoDesabilitado:
    @pytest.mark.parametrize("campo", ["asset", "check"])
    def test_desabilitado_nao_gera_incidente(self, engine, campo: str) -> None:
        with Session(engine) as session:
            asset = _asset(session, "srv", "192.0.2.10")
            check = _check(session, asset)
            if campo == "asset":
                asset.enabled = False
            else:
                check.enabled = False
            _resultados(session, check, [Status.DOWN] * 3)

            evaluate(session, now=AGORA)
            session.commit()
            assert session.exec(select(Incident)).all() == []
