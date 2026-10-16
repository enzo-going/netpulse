"""Parecer por IA: o pedido que sai para o provedor e o que volta para o operador.

O extra `ai` nao e instalado no CI, entao o SDK entra como um modulo falso com a
mesma forma (cliente, blocos de resposta e hierarquia de erros).
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from netpulse.analysis import AnalysisUnavailable, generate_analysis
from netpulse.config import Settings
from netpulse.models import (
    Asset,
    AssetKind,
    Check,
    CheckResult,
    CheckType,
    Incident,
    IncidentMember,
    Status,
)


class APIError(Exception):
    pass


class APIStatusError(APIError):
    status_code = 500


class BadRequestError(APIStatusError):
    status_code = 400


class AuthenticationError(APIStatusError):
    status_code = 401


class PermissionDeniedError(APIStatusError):
    status_code = 403


class NotFoundError(APIStatusError):
    status_code = 404


class RateLimitError(APIStatusError):
    status_code = 429


class APIConnectionError(APIError):
    pass


def _resposta(*blocos, stop_reason="end_turn", model="claude-sonnet-5"):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocos), model=model)


def _texto(texto):
    return SimpleNamespace(type="text", text=texto)


@pytest.fixture
def provedor(monkeypatch):
    """Instala o SDK falso; `provedor.resultado` sai de `beta.messages.create` (ou e levantado)."""
    estado = SimpleNamespace(pedidos=[], resultado=_resposta(_texto("Hipotese: uplink da filial.")))

    class Anthropic:
        def __init__(self, api_key):
            estado.api_key = api_key
            self.beta = self
            self.messages = self

        def create(self, **pedido):
            estado.pedidos.append(pedido)
            if isinstance(estado.resultado, Exception):
                raise estado.resultado
            return estado.resultado

    modulo = types.ModuleType("anthropic")
    modulo.Anthropic = Anthropic
    for erro in (
        APIError,
        APIStatusError,
        BadRequestError,
        AuthenticationError,
        PermissionDeniedError,
        NotFoundError,
        RateLimitError,
        APIConnectionError,
    ):
        setattr(modulo, erro.__name__, erro)
    monkeypatch.setitem(sys.modules, "anthropic", modulo)
    return estado


@pytest.fixture
def incidente(engine):
    with Session(engine) as session:
        asset = Asset(
            name="sw-filial", address="10.0.5.1", kind=AssetKind.SWITCH, location="Filial Centro"
        )
        session.add(asset)
        session.flush()
        check = Check(asset_id=asset.id, type=CheckType.PING)
        session.add(check)
        session.flush()
        session.add(
            CheckResult(check_id=check.id, status=Status.DOWN, error="sem resposta ao ping")
        )
        incident = Incident(title="Queda na 10.0.5.0/24", correlation_key="subnet:10.0.5.0/24")
        session.add(incident)
        session.flush()
        session.add(IncidentMember(incident_id=incident.id, asset_id=asset.id, check_id=check.id))
        session.commit()
        yield session, incident


def _settings():
    return Settings(ANTHROPIC_API_KEY="chave-de-teste", NETPULSE_AI_MODEL="claude-sonnet-5")


def test_pedido_nao_envia_amostragem_e_deixa_folga_para_o_pensamento(provedor, incidente) -> None:
    session, incident = incidente

    generate_analysis(session, incident, _settings())

    (pedido,) = provedor.pedidos
    # temperature/top_p/top_k fora do padrao devolvem 400 nos modelos atuais.
    assert not {"temperature", "top_p", "top_k"} & pedido.keys()
    assert pedido["model"] == "claude-sonnet-5"
    assert pedido["max_tokens"] >= 4000
    assert "dados nao confiaveis" in pedido["system"]
    contexto = pedido["messages"][0]["content"]
    assert "sw-filial" in contexto
    assert "local=Filial Centro" in contexto
    assert "observacao=sem resposta ao ping" in contexto


def test_modelo_padrao_vai_com_fallback_no_servidor(provedor, incidente) -> None:
    session, incident = incidente

    generate_analysis(session, incident, Settings(ANTHROPIC_API_KEY="chave-de-teste"))

    (pedido,) = provedor.pedidos
    assert pedido["model"] == "claude-opus-5-5"
    assert pedido["betas"] == ["server-side-fallback-2026-07-01"]
    assert pedido["fallbacks"] == "default"


@pytest.mark.parametrize("modelo", ["claude-sonnet-5", "claude-haiku-5-5"])
def test_modelo_sem_fallback_no_servidor_nao_recebe_o_parametro(
    provedor, incidente, modelo
) -> None:
    session, incident = incidente

    generate_analysis(
        session, incident, Settings(ANTHROPIC_API_KEY="chave-de-teste", NETPULSE_AI_MODEL=modelo)
    )

    (pedido,) = provedor.pedidos
    assert "fallbacks" not in pedido
    assert "betas" not in pedido


def test_parecer_registra_o_modelo_que_de_fato_respondeu(provedor, incidente) -> None:
    session, incident = incidente
    # Recusa no modelo pedido e resposta vinda do fallback.
    provedor.resultado = _resposta(_texto("Hipotese: uplink."), model="claude-opus-4-8")

    generate_analysis(session, incident, Settings(ANTHROPIC_API_KEY="chave-de-teste"))

    assert incident.analysis_model == "claude-opus-4-8"


def test_parecer_usa_so_os_blocos_de_texto_e_fica_no_incidente(provedor, incidente) -> None:
    session, incident = incidente
    provedor.resultado = _resposta(
        SimpleNamespace(type="thinking", thinking=""), _texto("Hipotese: uplink.")
    )

    texto = generate_analysis(session, incident, _settings())

    assert texto == "Hipotese: uplink."
    assert incident.analysis == "Hipotese: uplink."
    assert incident.analysis_model == "claude-sonnet-5"
    assert incident.analysis_at is not None


@pytest.mark.parametrize(
    ("erro", "mensagem"),
    [
        (AuthenticationError(), "chave ANTHROPIC_API_KEY foi recusada"),
        (NotFoundError(), "modelo claude-sonnet-5 nao existe"),
        (RateLimitError(), "limite de uso"),
        (BadRequestError(), "recusou o pedido"),
        (APIStatusError(), "erro 500"),
        (APIConnectionError(), "nao respondeu"),
    ],
)
def test_erro_do_provedor_vira_mensagem_util_e_nao_grava_parecer(
    provedor, incidente, erro, mensagem
) -> None:
    session, incident = incidente
    provedor.resultado = erro

    with pytest.raises(AnalysisUnavailable, match=mensagem):
        generate_analysis(session, incident, _settings())
    assert incident.analysis is None


@pytest.mark.parametrize(
    ("stop_reason", "mensagem"),
    [("refusal", "recusou gerar o parecer"), ("max_tokens", "incompleto")],
)
def test_recusa_ou_corte_nao_gravam_parecer_pela_metade(
    provedor, incidente, stop_reason, mensagem
) -> None:
    session, incident = incidente
    provedor.resultado = _resposta(_texto("Hipotese: upl"), stop_reason=stop_reason)

    with pytest.raises(AnalysisUnavailable, match=mensagem):
        generate_analysis(session, incident, _settings())
    assert incident.analysis is None


def test_sem_chave_nao_chama_o_provedor(provedor, incidente) -> None:
    session, incident = incidente

    with pytest.raises(AnalysisUnavailable, match="nao esta configurada"):
        generate_analysis(session, incident, Settings(ANTHROPIC_API_KEY=None))
    assert provedor.pedidos == []
