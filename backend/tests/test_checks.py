"""Testes dos checks.

O parser do ping e testado contra saidas reais gravadas, em vez de contra a rede:
e exatamente a parte que muda por sistema e por idioma, e a unica que da para
verificar de forma deterministica.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from netpulse.checks import available_types, get_runner
from netpulse.checks.base import CheckOutcome, CheckTarget, grade_latency
from netpulse.checks.ping import _build_command, check_ping, parse_ping_output
from netpulse.checks.snmp import check_snmp
from netpulse.checks.ssl_cert import _subject_field
from netpulse.checks.tcp import check_tcp
from netpulse.models import CheckType, Status

PING_OK_PT_BR = """
Disparando 192.0.2.1 com 32 bytes de dados:
Resposta de 192.0.2.1: bytes=32 tempo=12ms TTL=64

Estatisticas do Ping para 192.0.2.1:
    Pacotes: Enviados = 1, Recebidos = 1, Perdidos = 0 (0% de perda),
"""

PING_OK_SUBMILISSEGUNDO = "Resposta de 192.0.2.1: bytes=32 tempo<1ms TTL=128"

PING_OK_LINUX = """
PING 192.0.2.1 (192.0.2.1) 56(84) bytes of data.
64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=12.3 ms

--- 192.0.2.1 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
"""

PING_INACESSIVEL_PT_BR = """
Disparando 192.0.2.9 com 32 bytes de dados:
Resposta de 192.0.2.8: Host de destino inacessivel.
"""

PING_TIMEOUT_EN = """
Pinging 192.0.2.9 with 32 bytes of data:
Request timed out.
"""

PING_PERDA_TOTAL_LINUX = """
--- 192.0.2.9 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms
"""


class TestParsePing:
    @pytest.mark.parametrize(
        ("output", "latencia"),
        [
            (PING_OK_PT_BR, 12.0),
            (PING_OK_SUBMILISSEGUNDO, 1.0),
            (PING_OK_LINUX, 12.3),
        ],
    )
    def test_respostas_bem_sucedidas(self, output: str, latencia: float) -> None:
        outcome = parse_ping_output(0, output)
        assert outcome.status is Status.UP
        assert outcome.latency_ms == pytest.approx(latencia)
        assert outcome.error is None

    def test_aceita_virgula_como_separador_decimal(self) -> None:
        outcome = parse_ping_output(0, "Resposta de 192.0.2.1: tempo=1,5ms TTL=64")
        assert outcome.latency_ms == pytest.approx(1.5)

    @pytest.mark.parametrize(
        ("returncode", "output"),
        [
            (0, PING_INACESSIVEL_PT_BR),  # o Windows sai com 0 mesmo sem resposta util
            (1, PING_TIMEOUT_EN),
            (1, PING_PERDA_TOTAL_LINUX),
        ],
    )
    def test_falhas(self, returncode: int, output: str) -> None:
        outcome = parse_ping_output(returncode, output)
        assert outcome.status is Status.DOWN
        assert outcome.error

    def test_codigo_de_retorno_nao_zero_vence_a_latencia_encontrada(self) -> None:
        outcome = parse_ping_output(1, PING_OK_PT_BR)
        assert outcome.status is Status.DOWN


def test_build_command_inclui_o_endereco_e_uma_unica_sonda() -> None:
    cmd = _build_command("192.0.2.1", 3.0)
    assert cmd[0] == "ping"
    assert cmd[-1] == "192.0.2.1"
    assert "1" in cmd  # -n 1 no Windows, -c 1 nos demais


async def test_ping_em_endereco_local_nao_estoura(monkeypatch: pytest.MonkeyPatch) -> None:
    """O loopback responde em qualquer sistema; o que se verifica aqui e que o
    caminho completo (subprocesso + parser) devolve um resultado valido."""
    outcome = await check_ping(CheckTarget(address="127.0.0.1", timeout=3.0))
    assert outcome.status in (Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN)
    assert isinstance(outcome, CheckOutcome)


class TestGradeLatency:
    def test_sem_limiar_e_sempre_up(self) -> None:
        assert grade_latency(500.0, CheckTarget(address="x")) is Status.UP

    def test_acima_do_limiar_vira_degradado(self) -> None:
        target = CheckTarget(address="x", params={"degraded_above_ms": 100})
        assert grade_latency(150.0, target) is Status.DEGRADED

    def test_abaixo_do_limiar_continua_up(self) -> None:
        target = CheckTarget(address="x", params={"degraded_above_ms": 100})
        assert grade_latency(50.0, target) is Status.UP

    def test_sem_latencia_nao_degrada(self) -> None:
        target = CheckTarget(address="x", params={"degraded_above_ms": 100})
        assert grade_latency(None, target) is Status.UP


class TestTcp:
    async def test_porta_aberta(self) -> None:
        async def handle(_reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            outcome = await check_tcp(
                CheckTarget(address="127.0.0.1", params={"port": port}, timeout=3.0)
            )
        finally:
            server.close()
            await server.wait_closed()

        assert outcome.status is Status.UP
        assert outcome.latency_ms is not None
        assert outcome.detail["port"] == port

    async def test_porta_fechada(self) -> None:
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()

        outcome = await check_tcp(
            CheckTarget(address="127.0.0.1", params={"port": port}, timeout=3.0)
        )
        assert outcome.status is Status.DOWN
        assert outcome.error

    async def test_sem_porta_e_erro_de_configuracao(self) -> None:
        outcome = await check_tcp(CheckTarget(address="127.0.0.1"))
        assert outcome.status is Status.UNKNOWN
        assert "port" in (outcome.error or "")

    async def test_limiar_de_lentidao_se_aplica(self) -> None:
        async def handle(_reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            outcome = await check_tcp(
                CheckTarget(
                    address="127.0.0.1",
                    params={"port": port, "degraded_above_ms": 0},
                    timeout=3.0,
                )
            )
        finally:
            server.close()
            await server.wait_closed()

        assert outcome.status is Status.DEGRADED


class TestSslHelpers:
    def test_extrai_campo_do_subject(self) -> None:
        cert = {"subject": ((("countryName", "BR"),), (("commonName", "exemplo.org"),))}
        assert _subject_field(cert, "commonName") == "exemplo.org"

    def test_campo_ausente_devolve_none(self) -> None:
        assert _subject_field({"subject": ()}, "commonName") is None


@pytest.mark.skipif(
    importlib.util.find_spec("pysnmp") is not None,
    reason="pysnmp instalado; este teste cobre o caminho sem a dependencia opcional",
)
async def test_snmp_sem_dependencia_devolve_unknown() -> None:
    outcome = await check_snmp(CheckTarget(address="192.0.2.2"))
    assert outcome.status is Status.UNKNOWN
    assert "snmp" in (outcome.error or "").lower()


def test_registro_cobre_todos_os_tipos() -> None:
    assert set(available_types()) == set(CheckType)
    for check_type in CheckType:
        assert callable(get_runner(check_type))
