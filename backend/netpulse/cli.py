"""Interface de linha de comando do NetPulse."""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from netpulse import __version__
from netpulse.collector import Collector
from netpulse.config import get_settings
from netpulse.db import init_db, session_scope
from netpulse.demo import seed_demo
from netpulse.models import Asset, Check, CheckResult, Status
from netpulse.scheduler import DEFAULT_TICK_SECONDS, run_forever

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Monitoramento de ativos de rede com analise de incidentes por IA.",
)
console = Console()

_STATUS_STYLE = {
    Status.UP: "green",
    Status.DEGRADED: "yellow",
    Status.DOWN: "red",
    Status.UNKNOWN: "dim",
}


def _render_status(status: Status) -> str:
    return f"[{_STATUS_STYLE[status]}]{status.value}[/]"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def version() -> None:
    """Mostra a versao e o modo de operacao."""
    settings = get_settings()
    console.print(f"NetPulse [bold]{__version__}[/] — modo [bold]{settings.mode.value}[/]")
    console.print(f"banco: {settings.database_url}")
    console.print(f"analise por IA: {'habilitada' if settings.ai_enabled else 'desabilitada'}")


@app.command()
def init() -> None:
    """Cria o banco e as tabelas."""
    init_db()
    console.print(f"[green]Banco pronto em[/] {get_settings().database_url}")


@app.command()
def seed(
    force: bool = typer.Option(False, "--force", help="Cria mesmo se ja houver ativos."),
) -> None:
    """Popula o parque sintetico do modo demo."""
    init_db()
    with session_scope() as session:
        created = seed_demo(session, force=force)

    if created:
        console.print(f"[green]{created} ativo(s) criado(s).[/] Rode `netpulse run` para coletar.")
    else:
        console.print("[yellow]Nada criado[/] — o banco ja tem ativos. Use --force para inserir.")


@app.command()
def run(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Executa um ciclo de coleta e mostra o resultado."""
    _configure_logging(verbose)
    results = asyncio.run(Collector().run_once())

    if not results:
        console.print("[yellow]Nenhum check vencido neste momento.[/]")
        raise typer.Exit()

    counts: dict[Status, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    resumo = "  ".join(f"{_render_status(s)} {n}" for s, n in sorted(counts.items()))
    console.print(f"{len(results)} check(s) executado(s):  {resumo}")


@app.command()
def watch(
    tick: float = typer.Option(DEFAULT_TICK_SECONDS, "--tick", help="Segundos entre ciclos."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Roda o coletor continuamente ate Ctrl+C."""
    _configure_logging(verbose)
    settings = get_settings()
    console.print(
        f"Coletando em modo [bold]{settings.mode.value}[/] a cada {tick:g}s. Ctrl+C para parar."
    )
    try:
        asyncio.run(run_forever(tick_seconds=tick))
    except KeyboardInterrupt:
        console.print("\n[dim]Coleta interrompida.[/]")


@app.command()
def status() -> None:
    """Mostra o ultimo resultado de cada check."""
    table = Table(title="Estado atual", header_style="bold")
    table.add_column("Ativo")
    table.add_column("Endereco")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Latencia", justify="right")
    table.add_column("Coletado em")
    table.add_column("Observacao", overflow="fold")

    with session_scope() as session:
        rows = session.exec(
            select(Asset, Check).join(Check, Check.asset_id == Asset.id).order_by(Asset.name)
        ).all()

        if not rows:
            console.print("[yellow]Nenhum ativo cadastrado.[/] Rode `netpulse seed` primeiro.")
            raise typer.Exit()

        for asset, check in rows:
            latest = session.exec(
                select(CheckResult)
                .where(CheckResult.check_id == check.id)
                .order_by(CheckResult.ts.desc())
                .limit(1)
            ).first()

            if latest is None:
                table.add_row(
                    asset.name, asset.address, check.label, "[dim]sem coleta[/]", "", "", ""
                )
                continue

            table.add_row(
                asset.name,
                asset.address,
                check.label,
                _render_status(latest.status),
                f"{latest.latency_ms:.0f} ms" if latest.latency_ms is not None else "—",
                latest.ts.strftime("%d/%m %H:%M:%S"),
                latest.error or "",
            )

    console.print(table)


@app.command()
def assets() -> None:
    """Lista os ativos cadastrados."""
    table = Table(title="Ativos", header_style="bold")
    table.add_column("Nome")
    table.add_column("Endereco")
    table.add_column("Tipo")
    table.add_column("Sub-rede")
    table.add_column("Local")
    table.add_column("Checks", justify="right")

    with session_scope() as session:
        for asset in session.exec(select(Asset).order_by(Asset.name)).all():
            table.add_row(
                asset.name,
                asset.address,
                asset.kind.value,
                asset.subnet or "—",
                asset.location or "—",
                str(len(asset.checks)),
            )

    console.print(table)


def main() -> None:  # pragma: no cover - ponto de entrada
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
