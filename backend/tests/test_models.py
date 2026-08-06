from __future__ import annotations

import pytest
from sqlmodel import Session, select

from netpulse.models import (
    Asset,
    AssetKind,
    Check,
    CheckResult,
    CheckType,
    Status,
    derive_subnet,
    utcnow,
)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.0.2.10", "192.0.2.0/24"),
        ("198.51.100.255", "198.51.100.0/24"),
        ("srv-erp.exemplo.local", None),
        ("2001:db8::1", None),
        ("nao-e-um-ip", None),
    ],
)
def test_derive_subnet(address: str, expected: str | None) -> None:
    assert derive_subnet(address) == expected


def test_fill_subnet_nao_sobrescreve_valor_informado() -> None:
    asset = Asset(name="a", address="192.0.2.10", subnet="10.0.0.0/8")
    asset.fill_subnet()
    assert asset.subnet == "10.0.0.0/8"


def test_fill_subnet_deriva_quando_ausente() -> None:
    asset = Asset(name="a", address="192.0.2.10")
    asset.fill_subnet()
    assert asset.subnet == "192.0.2.0/24"


def test_status_is_failure() -> None:
    assert Status.DOWN.is_failure
    assert Status.DEGRADED.is_failure
    assert not Status.UP.is_failure
    assert not Status.UNKNOWN.is_failure


def test_check_label_inclui_a_porta() -> None:
    assert Check(asset_id=1, type=CheckType.TCP, params={"port": 443}).label == "tcp/443"
    assert Check(asset_id=1, type=CheckType.PING).label == "ping"


def test_persistencia_e_leitura(engine) -> None:
    with Session(engine) as session:
        asset = Asset(name="srv-erp", address="192.0.2.20", kind=AssetKind.SERVER, tags=["erp"])
        asset.fill_subnet()
        session.add(asset)
        session.flush()

        check = Check(asset_id=asset.id, type=CheckType.TCP, params={"port": 1433})
        session.add(check)
        session.flush()

        session.add(CheckResult(check_id=check.id, status=Status.UP, latency_ms=4.2, ts=utcnow()))
        session.commit()

    with Session(engine) as session:
        loaded = session.exec(select(Asset).where(Asset.name == "srv-erp")).one()
        assert loaded.subnet == "192.0.2.0/24"
        assert loaded.tags == ["erp"]
        assert len(loaded.checks) == 1
        assert loaded.checks[0].params == {"port": 1433}

        result = session.exec(select(CheckResult)).one()
        assert result.status is Status.UP
        assert result.latency_ms == pytest.approx(4.2)


def test_cascade_apaga_checks_e_resultados(engine) -> None:
    with Session(engine) as session:
        asset = Asset(name="descartavel", address="192.0.2.99")
        session.add(asset)
        session.flush()
        check = Check(asset_id=asset.id, type=CheckType.PING)
        session.add(check)
        session.flush()
        session.add(CheckResult(check_id=check.id, status=Status.UP))
        session.commit()

        session.delete(asset)
        session.commit()

        assert session.exec(select(Check)).all() == []
        assert session.exec(select(CheckResult)).all() == []
