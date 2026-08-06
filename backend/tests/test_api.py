"""Testes de integracao da API, contra um banco em memoria."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from netpulse.api.app import create_app
from netpulse.db import get_session
from netpulse.models import Asset, Check, CheckResult, CheckType, Status, utcnow


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    app = create_app(lifespan_enabled=False)

    def override() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client


def criar_ativo(
    engine, nome: str, endereco: str, checks: list[tuple[CheckType, dict]]
) -> list[int]:
    """Cria um ativo direto no banco e devolve os ids dos checks."""
    with Session(engine) as session:
        asset = Asset(name=nome, address=endereco)
        asset.fill_subnet()
        session.add(asset)
        session.flush()
        ids = []
        for tipo, params in checks:
            check = Check(asset_id=asset.id, type=tipo, params=params)
            session.add(check)
            session.flush()
            ids.append(check.id)
        session.commit()
        return ids


def gravar_resultado(engine, check_id: int, status: Status, **kwargs) -> None:
    with Session(engine) as session:
        session.add(CheckResult(check_id=check_id, status=status, **kwargs))
        session.commit()


class TestHealth:
    def test_responde_ok(self, client: TestClient) -> None:
        resposta = client.get("/api/health")
        assert resposta.status_code == 200
        corpo = resposta.json()
        assert corpo["status"] == "ok"
        assert corpo["mode"] == "demo"
        assert corpo["ai_enabled"] is False

    def test_raiz_redireciona_para_a_documentacao(self, client: TestClient) -> None:
        resposta = client.get("/", follow_redirects=False)
        assert resposta.status_code == 307
        assert resposta.headers["location"] == "/docs"


class TestCriacao:
    def test_cria_ativo_com_checks(self, client: TestClient) -> None:
        resposta = client.post(
            "/api/assets",
            json={
                "name": "srv-erp",
                "address": "192.0.2.20",
                "kind": "server",
                "tags": ["erp"],
                "checks": [{"type": "ping"}, {"type": "tcp", "params": {"port": 1433}}],
            },
        )
        assert resposta.status_code == 201
        corpo = resposta.json()
        assert corpo["subnet"] == "192.0.2.0/24"
        assert corpo["status"] == "unknown"  # ainda sem coleta
        assert len(corpo["checks"]) == 2
        assert {c["check"]["label"] for c in corpo["checks"]} == {"ping", "tcp/1433"}
        assert all(c["latest"] is None for c in corpo["checks"])

    def test_nome_duplicado_da_conflito(self, client: TestClient) -> None:
        payload = {"name": "srv-erp", "address": "192.0.2.20"}
        assert client.post("/api/assets", json=payload).status_code == 201
        resposta = client.post("/api/assets", json=payload)
        assert resposta.status_code == 409

    def test_check_tcp_sem_porta_e_rejeitado(self, client: TestClient) -> None:
        resposta = client.post(
            "/api/assets",
            json={"name": "x", "address": "192.0.2.5", "checks": [{"type": "tcp"}]},
        )
        assert resposta.status_code == 422
        assert "port" in resposta.text

    def test_nome_vazio_e_rejeitado(self, client: TestClient) -> None:
        resposta = client.post("/api/assets", json={"name": "", "address": "192.0.2.5"})
        assert resposta.status_code == 422

    def test_hostname_fica_sem_sub_rede(self, client: TestClient) -> None:
        resposta = client.post(
            "/api/assets", json={"name": "portal", "address": "portal.exemplo.org"}
        )
        assert resposta.status_code == 201
        assert resposta.json()["subnet"] is None


class TestListagem:
    def test_lista_vazia(self, client: TestClient) -> None:
        assert client.get("/api/assets").json() == []

    def test_estado_do_ativo_reflete_o_ultimo_resultado(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        gravar_resultado(engine, check_id, Status.DOWN, ts=utcnow() - timedelta(minutes=5))
        gravar_resultado(engine, check_id, Status.UP, latency_ms=4.0, ts=utcnow())

        corpo = client.get("/api/assets").json()
        assert corpo[0]["status"] == "up"
        assert corpo[0]["latency_ms"] == 4.0
        assert corpo[0]["checks"][0]["latest"]["status"] == "up"

    def test_ativo_assume_o_pior_estado_entre_os_checks(self, client: TestClient, engine) -> None:
        ping_id, tcp_id = criar_ativo(
            engine, "srv", "192.0.2.10", [(CheckType.PING, {}), (CheckType.TCP, {"port": 443})]
        )
        gravar_resultado(engine, ping_id, Status.UP, latency_ms=3.0)
        gravar_resultado(engine, tcp_id, Status.DOWN, error="porta fechada")

        corpo = client.get("/api/assets").json()
        assert corpo[0]["status"] == "down"

    def test_data_sai_em_utc_com_z(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        gravar_resultado(engine, check_id, Status.UP)

        corpo = client.get("/api/assets").json()
        assert corpo[0]["last_seen"].endswith("Z")
        assert corpo[0]["created_at"].endswith("Z")

    def test_filtra_por_estado(self, client: TestClient, engine) -> None:
        (bom,) = criar_ativo(engine, "bom", "192.0.2.10", [(CheckType.PING, {})])
        (ruim,) = criar_ativo(engine, "ruim", "192.0.2.11", [(CheckType.PING, {})])
        gravar_resultado(engine, bom, Status.UP)
        gravar_resultado(engine, ruim, Status.DOWN)

        nomes = [a["name"] for a in client.get("/api/assets", params={"estado": "down"}).json()]
        assert nomes == ["ruim"]

    def test_filtra_por_sub_rede(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "matriz", "192.0.2.10", [(CheckType.PING, {})])
        criar_ativo(engine, "filial", "198.51.100.10", [(CheckType.PING, {})])

        resposta = client.get("/api/assets", params={"subnet": "198.51.100.0/24"})
        assert [a["name"] for a in resposta.json()] == ["filial"]

    def test_busca_por_nome_ou_endereco(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "srv-erp", "192.0.2.10", [(CheckType.PING, {})])
        criar_ativo(engine, "sw-core", "192.0.2.99", [(CheckType.PING, {})])

        assert len(client.get("/api/assets", params={"busca": "erp"}).json()) == 1
        assert len(client.get("/api/assets", params={"busca": "192.0.2.99"}).json()) == 1


class TestDetalheEEdicao:
    def test_ativo_inexistente_da_404(self, client: TestClient) -> None:
        assert client.get("/api/assets/999").status_code == 404

    def test_atualiza_nome(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "antigo", "192.0.2.10", [(CheckType.PING, {})])
        asset_id = client.get("/api/assets").json()[0]["id"]

        resposta = client.patch(f"/api/assets/{asset_id}", json={"name": "novo"})
        assert resposta.status_code == 200
        assert resposta.json()["name"] == "novo"

    def test_trocar_o_endereco_recalcula_a_sub_rede(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        asset_id = client.get("/api/assets").json()[0]["id"]

        resposta = client.patch(f"/api/assets/{asset_id}", json={"address": "198.51.100.7"})
        assert resposta.json()["subnet"] == "198.51.100.0/24"

    def test_remove_o_ativo_e_seus_checks(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        asset_id = client.get("/api/assets").json()[0]["id"]

        assert client.delete(f"/api/assets/{asset_id}").status_code == 204
        assert client.get(f"/api/assets/{asset_id}").status_code == 404
        assert client.get("/api/assets").json() == []

    def test_adiciona_check_a_um_ativo(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        asset_id = client.get("/api/assets").json()[0]["id"]

        resposta = client.post(
            f"/api/assets/{asset_id}/checks",
            json={"type": "tcp", "params": {"port": 445}, "interval_seconds": 120},
        )
        assert resposta.status_code == 201
        assert resposta.json()["label"] == "tcp/445"
        assert len(client.get(f"/api/assets/{asset_id}").json()["checks"]) == 2


class TestChecks:
    def test_atualiza_o_intervalo(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        resposta = client.patch(f"/api/checks/{check_id}", json={"interval_seconds": 300})
        assert resposta.json()["interval_seconds"] == 300

    def test_intervalo_invalido_e_rejeitado(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        assert (
            client.patch(f"/api/checks/{check_id}", json={"interval_seconds": 1}).status_code == 422
        )

    def test_remove_o_check(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        assert client.delete(f"/api/checks/{check_id}").status_code == 204
        assert client.get(f"/api/checks/{check_id}").status_code == 404

    def test_historico_vem_em_ordem_cronologica(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        agora = utcnow()
        for minutos, latencia in ((30, 10.0), (20, 20.0), (10, 30.0)):
            gravar_resultado(
                engine,
                check_id,
                Status.UP,
                latency_ms=latencia,
                ts=agora - timedelta(minutes=minutos),
            )

        corpo = client.get(f"/api/checks/{check_id}/history").json()
        assert [p["latency_ms"] for p in corpo["points"]] == [10.0, 20.0, 30.0]

    def test_historico_respeita_a_janela(self, client: TestClient, engine) -> None:
        (check_id,) = criar_ativo(engine, "srv", "192.0.2.10", [(CheckType.PING, {})])
        gravar_resultado(engine, check_id, Status.UP, ts=utcnow() - timedelta(hours=48))
        gravar_resultado(engine, check_id, Status.UP, ts=utcnow())

        corpo = client.get(f"/api/checks/{check_id}/history", params={"horas": 24}).json()
        assert len(corpo["points"]) == 1

    def test_historico_de_check_inexistente_da_404(self, client: TestClient) -> None:
        assert client.get("/api/checks/999/history").status_code == 404


class TestOverview:
    def test_parque_vazio(self, client: TestClient) -> None:
        corpo = client.get("/api/overview").json()
        assert corpo["total_assets"] == 0
        assert corpo["open_incidents"] == 0
        assert corpo["degraded_or_down"] == []
        # Todos os estados aparecem, mesmo zerados.
        assert set(corpo["counts"]) == {"up", "down", "degraded", "unknown"}

    def test_conta_e_ordena_os_problemas(self, client: TestClient, engine) -> None:
        (bom,) = criar_ativo(engine, "bom", "192.0.2.10", [(CheckType.PING, {})])
        (lento,) = criar_ativo(engine, "lento", "192.0.2.11", [(CheckType.PING, {})])
        (caido,) = criar_ativo(engine, "caido", "192.0.2.12", [(CheckType.PING, {})])
        gravar_resultado(engine, bom, Status.UP)
        gravar_resultado(engine, lento, Status.DEGRADED, latency_ms=800.0)
        gravar_resultado(engine, caido, Status.DOWN, error="sem resposta")

        corpo = client.get("/api/overview").json()
        assert corpo["total_assets"] == 3
        assert corpo["counts"]["up"] == 1
        assert corpo["counts"]["degraded"] == 1
        assert corpo["counts"]["down"] == 1
        # O pior vem primeiro.
        assert [a["name"] for a in corpo["degraded_or_down"]] == ["caido", "lento"]

    def test_ativo_sem_coleta_conta_como_desconhecido(self, client: TestClient, engine) -> None:
        criar_ativo(engine, "novo", "192.0.2.10", [(CheckType.PING, {})])
        corpo = client.get("/api/overview").json()
        assert corpo["counts"]["unknown"] == 1
        assert corpo["degraded_or_down"] == []


class TestIncidentes:
    def test_lista_vazia_enquanto_o_motor_nao_existe(self, client: TestClient) -> None:
        assert client.get("/api/incidents").json() == []

    def test_incidente_inexistente_da_404(self, client: TestClient) -> None:
        assert client.get("/api/incidents/1").status_code == 404


def test_openapi_e_gerado(client: TestClient) -> None:
    esquema = client.get("/openapi.json").json()
    assert esquema["info"]["title"] == "NetPulse"
    assert "/api/assets" in esquema["paths"]
    assert "/api/overview" in esquema["paths"]
