"""Check de ICMP.

Usa o binario `ping` do sistema em vez de socket raw de proposito: socket raw
exige privilegio de administrador (ou CAP_NET_RAW), e exigir isso so para rodar
o projeto afastaria quem vai apenas experimentar. O custo e ter de interpretar a
saida do comando, que muda por sistema e por idioma — dai as duas listas abaixo.
"""

from __future__ import annotations

import asyncio
import math
import platform
import re

from netpulse.checks.base import CheckOutcome, CheckTarget, grade_latency, register
from netpulse.models import CheckType, Status

_IS_WINDOWS = platform.system() == "Windows"

# "time=12.3 ms", "tempo=12ms", "tempo<1ms"
_RTT_RE = re.compile(r"(?:time|tempo)\s*[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", re.IGNORECASE)

# O ping do Windows as vezes sai com codigo 0 mesmo sem resposta util, entao o
# codigo de retorno sozinho nao decide. Os trechos abaixo sao cortados antes de
# qualquer acento, de proposito: assim continuam casando mesmo quando a saida foi
# decodificada de forma imperfeita.
_FAILURE_MARKERS = (
    "unreachable",
    "inacess",
    "timed out",
    "esgotado",
    "expirou",
    "100% packet loss",
    "100% de perda",
    "unknown host",
    "name or service not known",
    "could not find host",
    "nao foi possivel",
    "o foi poss",
)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp850", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _build_command(address: str, timeout: float) -> list[str]:
    if _IS_WINDOWS:
        return ["ping", "-n", "1", "-w", str(int(timeout * 1000)), address]
    # O -W do ping do Linux e em segundos e nao aceita fracao.
    return ["ping", "-c", "1", "-W", str(max(1, math.ceil(timeout))), address]


def parse_ping_output(returncode: int, output: str) -> CheckOutcome:
    """Traduz a saida do ping num resultado. Separado para poder ser testado
    contra saidas reais gravadas, sem depender da rede."""
    lowered = output.lower()
    failed = returncode != 0 or any(marker in lowered for marker in _FAILURE_MARKERS)

    match = _RTT_RE.search(output)
    latency = float(match.group(1).replace(",", ".")) if match else None

    if failed:
        return CheckOutcome(
            status=Status.DOWN,
            latency_ms=latency,
            detail={"returncode": returncode},
            error="host nao respondeu ao ICMP",
        )

    return CheckOutcome(
        status=Status.UP,
        latency_ms=latency,
        detail={"returncode": returncode},
    )


@register(CheckType.PING)
async def check_ping(target: CheckTarget) -> CheckOutcome:
    cmd = _build_command(target.address, target.timeout)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return CheckOutcome.unknown("comando `ping` nao encontrado no sistema")
    except OSError as exc:
        return CheckOutcome.unknown(f"falha ao executar o ping: {exc}")

    try:
        # A margem sobre o timeout cobre o tempo de criar o processo; sem ela o
        # ping seria morto antes de reportar o proprio timeout.
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=target.timeout + 2)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return CheckOutcome.down("o ping excedeu o tempo limite", timeout=target.timeout)

    outcome = parse_ping_output(proc.returncode or 0, _decode(raw))
    if outcome.status is Status.UP:
        outcome.status = grade_latency(outcome.latency_ms, target)
    return outcome
