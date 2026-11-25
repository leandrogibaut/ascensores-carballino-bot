"""El webhook exige ?clave= cuando WEBHOOK_CLAVE está configurada."""
import logging
import os

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("WHATSAPP_PROVIDER", "zapi")
os.environ.setdefault("ZAPI_INSTANCE_ID", "prueba")
os.environ.setdefault("ZAPI_TOKEN", "prueba")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-tests.db")

from agent import main  # noqa: E402

# Mensaje propio (fromMe) en chat privado: el handler solo silencia y responde.
PAYLOAD = {"fromMe": True, "isGroup": False, "fromApi": False, "phone": "5491100000000"}


@pytest.fixture
def cliente(monkeypatch):
    silenciados = []

    async def silenciar(numero, horas, motivo):
        silenciados.append(numero)

    monkeypatch.setattr(main, "es_intervencion_humana", lambda _p: True)
    monkeypatch.setattr(main, "extraer_numero_conversacion", lambda _p: "5491100000000")
    monkeypatch.setattr(main, "silenciar_conversacion", silenciar)
    c = TestClient(main.app)
    c.silenciados = silenciados
    return c


@pytest.mark.parametrize("ruta", ["/webhook", "/webhook/messages"])
def test_sin_variable_acepta_como_antes(cliente, monkeypatch, ruta):
    monkeypatch.delenv("WEBHOOK_CLAVE", raising=False)
    r = cliente.post(ruta, json=PAYLOAD)
    assert r.status_code == 200
    assert cliente.silenciados == ["5491100000000"]


@pytest.mark.parametrize("ruta", ["/webhook", "/webhook/messages"])
def test_con_variable_y_sin_clave_rechaza_sin_procesar(cliente, monkeypatch, ruta):
    monkeypatch.setenv("WEBHOOK_CLAVE", "clave-z")
    r = cliente.post(ruta, json=PAYLOAD)
    assert r.status_code == 401
    assert cliente.silenciados == []


def test_con_clave_incorrecta_rechaza_sin_procesar(cliente, monkeypatch):
    monkeypatch.setenv("WEBHOOK_CLAVE", "clave-z")
    r = cliente.post("/webhook?clave=otra", json=PAYLOAD)
    assert r.status_code == 401
    assert cliente.silenciados == []


@pytest.mark.parametrize("ruta", ["/webhook", "/webhook/messages"])
def test_con_clave_correcta_procesa(cliente, monkeypatch, ruta):
    monkeypatch.setenv("WEBHOOK_CLAVE", "clave-z")
    r = cliente.post(f"{ruta}?clave=clave-z", json=PAYLOAD)
    assert r.status_code == 200
    assert cliente.silenciados == ["5491100000000"]


def test_clave_por_header_no_alcanza(cliente, monkeypatch):
    monkeypatch.setenv("WEBHOOK_CLAVE", "clave-z")
    r = cliente.post("/webhook", json=PAYLOAD, headers={"clave": "clave-z"})
    assert r.status_code == 401


def test_get_de_verificacion_no_cambia(cliente, monkeypatch):
    monkeypatch.setenv("WEBHOOK_CLAVE", "clave-z")
    assert cliente.get("/webhook").status_code == 200


def test_registro_de_acceso_oculta_la_clave():
    filtro = main._OcultarClaveWebhook()
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", "/webhook?clave=secreta123&x=1", "1.1", 200),
        None,
    )
    assert filtro.filter(record)
    texto = record.getMessage()
    assert "secreta123" not in texto
    assert "clave=***&x=1" in texto
    assert any(isinstance(f, main._OcultarClaveWebhook)
               for f in logging.getLogger("uvicorn.access").filters)
