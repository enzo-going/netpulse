from __future__ import annotations

from datetime import datetime, timedelta, timezone

from netpulse.cli import _local_time

BRASILIA = timezone(timedelta(hours=-3))


def test_local_time_converte_utc_do_banco_para_o_fuso_local() -> None:
    # 17:45 UTC, como o banco guarda (sem fuso), e 14:45 em Brasilia.
    assert _local_time(datetime(2026, 10, 15, 17, 45, 44), BRASILIA) == "15/10 14:45:44"


def test_local_time_vira_o_dia_quando_o_fuso_atravessa_a_meia_noite() -> None:
    assert _local_time(datetime(2026, 10, 16, 1, 30, 0), BRASILIA) == "15/10 22:30:00"
