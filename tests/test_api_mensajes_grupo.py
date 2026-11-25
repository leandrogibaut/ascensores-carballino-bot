"""La lectura de mensajes del grupo de técnicos exige la clave API_LECTURA_CLAVE."""
import os

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("WHATSAPP_PROVIDER", "zapi")
os.environ.setdefault("ZAPI_INSTANCE_ID", "prueba")
os.environ.setdefault("ZAPI_TOKEN", "prueba")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-tests.db")

from agent import main  # noqa: E402

URL = "/api/mensajes-grupo-hoy"


class _Msg:
    telefono_remitente = "5491100000000"
    nombre_remitente = "Técnico"
    texto = "Listo"

    class timestamp:  # noqa: N801
        @staticmethod
        def strftime(_fmt):
            return "10:00"


@pytest.fixture
def cliente(monkeypatch):
    async def mensajes_falsos():
        return [_Msg()]

    monkeypatch.setattr(main, "obtener_mensajes_grupo_del_dia", mensajes_falsos)
    # Sin "with": no arranca el scheduler ni la base (lifespan).
    return TestClient(main.app)


def test_sin_variable_configurada_queda_cerrado(cliente, monkeypatch):
    monkeypatch.delenv("API_LECTURA_CLAVE", raising=False)
    assert cliente.get(URL).status_code == 401
    assert cliente.get(URL, headers={"X-Api-Key": "cualquiera"}).status_code == 401


def test_sin_clave_no_devuelve_mensajes(cliente, monkeypatch):
    monkeypatch.setenv("API_LECTURA_CLAVE", "clave-correcta")
    r = cliente.get(URL)
    assert r.status_code == 401
    assert "Listo" not in r.text


def test_clave_incorrecta_no_devuelve_mensajes(cliente, monkeypatch):
    monkeypatch.setenv("API_LECTURA_CLAVE", "clave-correcta")
    r = cliente.get(URL, headers={"X-Api-Key": "otra"})
    assert r.status_code == 401
    assert "Listo" not in r.text


def test_clave_por_url_no_sirve(cliente, monkeypatch):
    monkeypatch.setenv("API_LECTURA_CLAVE", "clave-correcta")
    assert cliente.get(URL + "?X-Api-Key=clave-correcta").status_code == 401


def test_clave_correcta_devuelve_mensajes(cliente, monkeypatch):
    monkeypatch.setenv("API_LECTURA_CLAVE", "clave-correcta")
    r = cliente.get(URL, headers={"X-Api-Key": "clave-correcta"})
    assert r.status_code == 200
    assert r.json() == [
        {"telefono": "5491100000000", "nombre": "Técnico", "texto": "Listo", "hora": "10:00"}
    ]


def test_health_check_sigue_publico(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert r.json()["agente"] == "Olivia"
