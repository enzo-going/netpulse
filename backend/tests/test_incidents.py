from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session, select

from netpulse.incidents import process_results
from netpulse.models import (
    Asset,
    Check,
    CheckResult,
    CheckType,
    Incident,
    IncidentMember,
    IncidentStatus,
    Severity,
    Status,
    utcnow,
)


def make_check(
    session: Session,
    name: str,
    address: str,
    *,
    location: str | None = None,
) -> Check:
    asset = Asset(name=name, address=address, location=location)
    asset.fill_subnet()
    session.add(asset)
    session.flush()
    check = Check(asset_id=asset.id, type=CheckType.PING)
    session.add(check)
    session.flush()
    return check


def add_result(session: Session, check: Check, status: Status, *, offset: int = 0) -> CheckResult:
    result = CheckResult(
        check_id=check.id,
        status=status,
        ts=utcnow() + timedelta(seconds=offset),
    )
    session.add(result)
    session.flush()
    return result


def process(session: Session, results: list[CheckResult], *, threshold: int = 1, window: int = 180):
    return process_results(
        session,
        results,
        failure_threshold=threshold,
        correlation_window=window,
    )


def test_exige_falhas_consecutivas_antes_de_abrir(engine) -> None:
    with Session(engine) as session:
        check = make_check(session, "srv", "192.0.2.10")
        first = add_result(session, check, Status.DOWN, offset=1)
        second = add_result(session, check, Status.DOWN, offset=2)
        process(session, [first, second], threshold=3)
        assert session.exec(select(Incident)).all() == []

        third = add_result(session, check, Status.DOWN, offset=3)
        process(session, [third], threshold=3)
        incident = session.exec(select(Incident)).one()
        assert incident.status is IncidentStatus.OPEN
        assert incident.title == "Falha confirmada em srv"


def test_resultado_up_quebra_a_sequencia_de_falhas(engine) -> None:
    with Session(engine) as session:
        check = make_check(session, "srv", "192.0.2.10")
        add_result(session, check, Status.DOWN, offset=1)
        add_result(session, check, Status.UP, offset=2)
        last = add_result(session, check, Status.DOWN, offset=3)
        process(session, [last], threshold=2)
        assert session.exec(select(Incident)).all() == []


def test_agrupa_quedas_da_mesma_subrede(engine) -> None:
    with Session(engine) as session:
        first_check = make_check(session, "sw-filial", "198.51.100.2")
        second_check = make_check(session, "pc-filial", "198.51.100.20")
        at = 10
        results = [
            add_result(session, first_check, Status.DOWN, offset=at),
            add_result(session, second_check, Status.DOWN, offset=at),
        ]
        process(session, results)

        incident = session.exec(select(Incident)).one()
        members = session.exec(select(IncidentMember)).all()
        assert incident.correlation_key == "subnet:198.51.100.0/24"
        assert incident.severity is Severity.CRITICAL
        assert incident.title == "Queda correlacionada em 2 ativos — 198.51.100.0/24"
        assert len(members) == 2


def test_hostnames_caidos_agrupam_por_localizacao(engine) -> None:
    with Session(engine) as session:
        a = make_check(session, "erp", "erp.local", location="Filial Norte")
        b = make_check(session, "dns", "dns.local", location="Filial Norte")
        process(
            session,
            [add_result(session, a, Status.DOWN), add_result(session, b, Status.DOWN)],
        )
        assert session.exec(select(Incident)).one().correlation_key == "location:filial-norte"


def test_degradacoes_na_mesma_subrede_nao_sao_correlacionadas(engine) -> None:
    with Session(engine) as session:
        a = make_check(session, "certificado", "203.0.113.10")
        b = make_check(session, "latencia", "203.0.113.11")
        process(
            session,
            [add_result(session, a, Status.DEGRADED), add_result(session, b, Status.DEGRADED)],
        )
        incidents = session.exec(select(Incident)).all()
        assert len(incidents) == 2
        assert {item.correlation_key for item in incidents} == {f"check:{a.id}", f"check:{b.id}"}


def test_nao_duplica_membro_enquanto_a_falha_continua(engine) -> None:
    with Session(engine) as session:
        check = make_check(session, "srv", "192.0.2.10")
        process(session, [add_result(session, check, Status.DOWN, offset=1)])
        process(session, [add_result(session, check, Status.DOWN, offset=2)])
        assert len(session.exec(select(Incident)).all()) == 1
        assert len(session.exec(select(IncidentMember)).all()) == 1


def test_resolve_somente_quando_todos_os_membros_recuperam(engine) -> None:
    with Session(engine) as session:
        a = make_check(session, "a", "198.51.100.10")
        b = make_check(session, "b", "198.51.100.11")
        process(
            session,
            [add_result(session, a, Status.DOWN), add_result(session, b, Status.DOWN)],
        )
        incident = session.exec(select(Incident)).one()

        process(session, [add_result(session, a, Status.UP, offset=60)])
        assert incident.status is IncidentStatus.OPEN

        process(session, [add_result(session, b, Status.UP, offset=61)])
        assert incident.status is IncidentStatus.RESOLVED
        assert incident.resolved_at is not None
        assert all(
            member.recovered_at is not None for member in session.exec(select(IncidentMember)).all()
        )


def test_unknown_nao_resolve_incidente(engine) -> None:
    with Session(engine) as session:
        check = make_check(session, "srv", "192.0.2.10")
        process(session, [add_result(session, check, Status.DOWN)])
        process(session, [add_result(session, check, Status.UNKNOWN, offset=60)])
        assert session.exec(select(Incident)).one().status is IncidentStatus.OPEN


def test_falha_fora_da_janela_abre_outro_incidente(engine) -> None:
    with Session(engine) as session:
        a = make_check(session, "a", "198.51.100.10")
        b = make_check(session, "b", "198.51.100.11")
        process(session, [add_result(session, a, Status.DOWN, offset=0)], window=30)
        process(session, [add_result(session, b, Status.DOWN, offset=60)], window=30)
        assert len(session.exec(select(Incident)).all()) == 2


def test_recaida_dentro_do_incidente_reabre_o_membro(engine) -> None:
    """Um host que oscila nao pode derrubar o processamento.

    O membro ja existe no incidente; inserir outro violaria a unicidade
    (incident_id, check_id) e, como process_results roda na mesma transacao da
    coleta, levaria junto os resultados daquele ciclo.
    """
    with Session(engine) as session:
        instavel = make_check(session, "instavel", "198.51.100.1")
        vizinho = make_check(session, "vizinho", "198.51.100.2")

        process(
            session,
            [
                add_result(session, instavel, Status.DOWN),
                add_result(session, vizinho, Status.DOWN),
            ],
        )

        # O instavel volta, mas o vizinho segue caido: o incidente continua aberto.
        process(session, [add_result(session, instavel, Status.UP, offset=60)])
        assert session.exec(select(Incident)).one().status is IncidentStatus.OPEN

        # E cai de novo, ainda dentro do mesmo incidente.
        process(session, [add_result(session, instavel, Status.DOWN, offset=120)])

        membros = session.exec(
            select(IncidentMember).where(IncidentMember.check_id == instavel.id)
        ).all()
        assert len(membros) == 1, "a participacao foi duplicada em vez de reaberta"
        assert membros[0].recovered_at is None
