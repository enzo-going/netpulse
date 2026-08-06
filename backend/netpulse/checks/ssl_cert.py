"""Check de certificado TLS: validade da cadeia e dias ate o vencimento.

Certificado vencendo e a falha mais previsivel de um parque — e a unica que da
para avisar com semanas de antecedencia. Por isso o resultado nao e so
"conectou": faltando menos que `warn_days`, o servico ja entra como degradado.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from datetime import UTC, datetime
from time import perf_counter

from netpulse.checks.base import CheckOutcome, CheckTarget, grade_latency, register
from netpulse.models import CheckType, Status

DEFAULT_PORT = 443
DEFAULT_WARN_DAYS = 21


def _subject_field(cert: dict, field: str) -> str | None:
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == field:
                return value
    return None


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(TimeoutError, OSError, ssl.SSLError):
        await writer.wait_closed()


@register(CheckType.SSL)
async def check_ssl(target: CheckTarget) -> CheckOutcome:
    port = int(target.param("port", DEFAULT_PORT))
    server_name = target.param("server_name") or target.address
    warn_days = int(target.param("warn_days", DEFAULT_WARN_DAYS))

    context = ssl.create_default_context()
    started = perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target.address, port, ssl=context, server_hostname=server_name),
            timeout=target.timeout,
        )
    except TimeoutError:
        return CheckOutcome.down(f"timeout no handshake TLS com {target.address}:{port}", port=port)
    except ssl.SSLCertVerificationError as exc:
        # Cadeia invalida, hostname divergente ou certificado ja vencido.
        return CheckOutcome.down(f"certificado invalido: {exc.verify_message or exc}", port=port)
    except ssl.SSLError as exc:
        return CheckOutcome.down(f"erro de TLS: {exc}", port=port)
    except (OSError, ValueError) as exc:
        return CheckOutcome.down(f"falha ao conectar em {port}: {exc}", port=port)

    latency = (perf_counter() - started) * 1000
    ssl_object = writer.get_extra_info("ssl_object")
    cert = ssl_object.getpeercert() if ssl_object is not None else None
    await _close(writer)

    if not cert or "notAfter" not in cert:
        return CheckOutcome(
            status=grade_latency(latency, target),
            latency_ms=latency,
            detail={"port": port},
            error="handshake concluido, mas o certificado nao pode ser lido",
        )

    expires_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=UTC)
    days_left = (expires_at - datetime.now(UTC)).total_seconds() / 86400

    detail = {
        "port": port,
        "expires_at": expires_at.isoformat(),
        "days_left": round(days_left, 1),
        "issuer": _subject_field({"subject": cert.get("issuer", ())}, "organizationName"),
        "common_name": _subject_field(cert, "commonName"),
    }

    if days_left <= 0:
        return CheckOutcome(
            status=Status.DOWN,
            latency_ms=latency,
            detail=detail,
            error=f"certificado vencido em {expires_at.date().isoformat()}",
        )

    if days_left <= warn_days:
        return CheckOutcome(
            status=Status.DEGRADED,
            latency_ms=latency,
            detail=detail,
            error=f"certificado vence em {int(days_left)} dia(s)",
        )

    return CheckOutcome(status=grade_latency(latency, target), latency_ms=latency, detail=detail)
