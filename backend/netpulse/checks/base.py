"""Contrato comum dos checks.

Um check e uma funcao assincrona pura: recebe um alvo, devolve um resultado e nao
toca no banco. Isso mantem a coleta testavel sem rede e sem persistencia.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from netpulse.models import CheckType, Status


@dataclass(frozen=True, slots=True)
class CheckTarget:
    """O que um check precisa saber para rodar."""

    address: str
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 5.0

    def param(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)


@dataclass(slots=True)
class CheckOutcome:
    """O que um check devolve."""

    status: Status
    latency_ms: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def down(cls, error: str, **detail: Any) -> CheckOutcome:
        return cls(status=Status.DOWN, error=error, detail=detail)

    @classmethod
    def unknown(cls, error: str, **detail: Any) -> CheckOutcome:
        return cls(status=Status.UNKNOWN, error=error, detail=detail)


CheckFn = Callable[[CheckTarget], Awaitable[CheckOutcome]]

_REGISTRY: dict[CheckType, CheckFn] = {}


def register(check_type: CheckType) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        _REGISTRY[check_type] = fn
        return fn

    return decorator


def get_runner(check_type: CheckType) -> CheckFn:
    try:
        return _REGISTRY[check_type]
    except KeyError:  # pragma: no cover - protegido pelo Enum
        raise ValueError(f"check sem implementacao registrada: {check_type}") from None


def available_types() -> list[CheckType]:
    return sorted(_REGISTRY, key=lambda t: t.value)


def grade_latency(latency_ms: float | None, target: CheckTarget) -> Status:
    """Classifica um check que respondeu, aplicando o limiar de lentidao.

    Um ativo que responde mas responde devagar e um sinal util — e costuma ser o
    aviso que antecede a queda.
    """
    threshold = target.param("degraded_above_ms")
    if threshold is not None and latency_ms is not None and latency_ms > float(threshold):
        return Status.DEGRADED
    return Status.UP
