"""Montagem da aplicacao FastAPI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from netpulse import __version__
from netpulse.api.routers import assets, checks, incidents, overview
from netpulse.config import get_settings
from netpulse.db import init_db

# O front em desenvolvimento roda no Vite; em producao ele e servido pela mesma
# origem e o CORS deixa de importar.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

DESCRIPTION = """
API do NetPulse.

Monitora ativos de rede, guarda a serie historica de cada verificacao e agrupa
falhas simultaneas da mesma sub-rede em incidentes.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app(*, lifespan_enabled: bool = True) -> FastAPI:
    """Cria a aplicacao.

    `lifespan_enabled=False` serve aos testes, que montam o proprio banco e nao
    querem que a aplicacao crie tabelas no banco configurado no ambiente.
    """
    app = FastAPI(
        title="NetPulse",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan if lifespan_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(overview.router)
    app.include_router(assets.router)
    app.include_router(checks.router)
    app.include_router(incidents.router)

    frontend_dir = get_settings().frontend_dir
    frontend_path = Path(frontend_dir) if frontend_dir else None
    if frontend_path is not None and (frontend_path / "index.html").is_file():
        # Montado por ultimo: /api e /docs continuam com precedencia. `html=True`
        # entrega index.html na raiz e mantem o dashboard na mesma origem da API.
        app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        def raiz() -> RedirectResponse:
            return RedirectResponse("/docs")

    return app


app = create_app()
