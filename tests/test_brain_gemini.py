"""Paso de Olivia pública a Gemini (Ollama dado de baja, 25/11/2026).

- Modelo por defecto gemini-4-hydrogen, con reasoning_effort "low" (sin el ajuste, en 5 de 40
  conversaciones reales pensaba tanto que se quedaba sin lugar y devolvía la respuesta vacía).
- Si Gemini falla o devuelve vacío, reintenta una vez; si vuelve a fallar, mensaje de error fijo.
  Ya no cae a Ollama (dado de baja).
- Con LLM_PROVIDER=ollama sigue funcionando igual (vuelta atrás mientras dure Ollama).
"""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from agent import brain


class ClienteFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)  # str, None o Exception, una por llamada
        self.llamadas = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=r))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


@pytest.fixture
def gemini(monkeypatch):
    def armar(*respuestas):
        g, o = ClienteFalso(respuestas), ClienteFalso(["respuesta de ollama"])
        monkeypatch.setattr(brain, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(brain, "_gemini_client", g)
        monkeypatch.setattr(brain, "_ollama_client", o)
        return g, o
    return armar


def responder():
    return asyncio.run(brain.generar_respuesta("hola, se trabó el ascensor", []))


def test_modelo_y_ajuste_por_defecto(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_REASONING_EFFORT", raising=False)
    recargado = importlib.reload(brain)
    try:
        assert recargado.GEMINI_MODEL == "gemini-4-hydrogen"
        assert recargado.GEMINI_REASONING_EFFORT == "low"
    finally:
        importlib.reload(brain)


def test_gemini_usa_modelo_y_pensar_poco(gemini, monkeypatch):
    monkeypatch.setattr(brain, "GEMINI_MODEL", "gemini-4-hydrogen")
    monkeypatch.setattr(brain, "GEMINI_REASONING_EFFORT", "low")
    g, o = gemini("Perfecto, el reclamo quedó registrado.")
    assert responder() == "Perfecto, el reclamo quedó registrado."
    assert g.llamadas[0]["model"] == "gemini-4-hydrogen"
    assert g.llamadas[0]["reasoning_effort"] == "low"
    assert o.llamadas == []


def test_ajuste_vacio_no_se_manda(gemini, monkeypatch):
    monkeypatch.setattr(brain, "GEMINI_REASONING_EFFORT", "")
    g, _ = gemini("ok")
    responder()
    assert "reasoning_effort" not in g.llamadas[0]


@pytest.mark.parametrize("primera", [None, "", "   ", RuntimeError("503")])
def test_vacio_o_error_reintenta_una_vez(gemini, primera):
    g, o = gemini(primera, "Buen día, ¿cuál es la dirección?")
    assert responder() == "Buen día, ¿cuál es la dirección?"
    assert len(g.llamadas) == 2
    assert o.llamadas == []


def test_dos_fallas_da_mensaje_de_error_sin_ollama(gemini):
    g, o = gemini(RuntimeError("caído"), None)
    assert responder() == brain.obtener_mensaje_error()
    assert len(g.llamadas) == 2
    assert o.llamadas == []


def test_ollama_sigue_andando_como_vuelta_atras(monkeypatch):
    o = ClienteFalso(["respuesta de ollama"])
    monkeypatch.setattr(brain, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(brain, "_ollama_client", o)
    assert responder() == "respuesta de ollama"
    assert "reasoning_effort" not in o.llamadas[0]
