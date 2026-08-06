"""Implementacoes de check.

Importar este pacote registra todos os tipos disponiveis no registro de
`netpulse.checks.base`.
"""

from netpulse.checks import ping, snmp, ssl_cert, tcp  # noqa: F401
from netpulse.checks.base import (
    CheckOutcome,
    CheckTarget,
    available_types,
    get_runner,
    register,
)

__all__ = [
    "CheckOutcome",
    "CheckTarget",
    "available_types",
    "get_runner",
    "register",
]
