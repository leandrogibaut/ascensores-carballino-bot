"""Regla de negocio: persona encerrada/atrapada -> texto FIJO, nunca el del LLM.

1) pregunta si sigue encerrada o ya salió; 2) da 4301-3967 y 1565024510;
3) si sigue encerrada, WhatsApp no es el medio: llamar sí o sí; 4) pide la
dirección si falta. El recordatorio de dirección repite los teléfonos. Si nadie
está encerrado, no se pregunta por personas encerradas.
"""

import asyncio
import os

import pytest

os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("WHATSAPP_PROVIDER", "zapi")
os.environ.setdefault("ZAPI_INSTANCE_ID", "prueba")
os.environ.setdefault("ZAPI_TOKEN", "prueba")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-tests.db")

from agent import main  # noqa: E402
from agent.reclamos import (  # noqa: E402
    es_emergencia_critica,
    menciona_persona_encerrada,
    menciona_rescate,
)

TEXTO_LLM = "TEXTO DEL LLM QUE NO DEBE LLEGAR AL CLIENTE"


# ── Detección ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "Hay una persona encerrada en el ascensor",
    "estoy atrapada en el asc",
    "ayer quedó gente encerrada, ya los sacaron, hoy sigue parado",
    "ya la sacaron, estuvo encerrada media hora",
    "mi hijo no puede salir del ascensor",
    "se clavó con mi abuela",
    "quedó mi mamá adentro",
    "la cabina quedó trabada entre el 3 y el 4 con mi vieja",
])
def test_menciona_persona_encerrada(texto):
    assert menciona_persona_encerrada(texto)
    assert es_emergencia_critica(texto)


@pytest.mark.parametrize("texto", [
    "sale humo del motor",
    "chispea el tablero",
    "el ascensor quedó entre pisos",
    "se paró entre el 5 y el 6",
    "el ascensor está parado en PB",
    "la puerta está trabada",
    "¿van a venir hoy?",
])
def test_no_menciona_persona_encerrada(texto):
    assert not menciona_persona_encerrada(texto)


@pytest.mark.parametrize("texto", [
    "ya la sacaron",
    "el encargado los sacó y lo dejó fuera de servicio",
    "ya salió",
    "ya salieron todos",
    "ya están afuera",
    "ya la liberaron",
    "la rescataron los bomberos",
    "ya pudo salir",
    "ya fue rescatada",
    "ayer quedó gente encerrada, ya los sacaron, hoy sigue parado",
])
def test_menciona_rescate(texto):
    assert menciona_rescate(texto)


@pytest.mark.parametrize("texto", [
    "hay una persona encerrada",
    "todavía no la sacaron",
    "aún no la sacaron",
    "nadie la sacó",
    "no pudo salir",
    "no sé si ya salió",
    "¿ya la sacaron?",
    "¿pudo salir?",
    "ya la sacaron pero sigue encerrada la otra señora",
    "sigue adentro, ya la sacaron?",
    "está esperando ser rescatada",
    "salieron chispas del tablero",
])
def test_rescate_dudoso_o_ausente(texto):
    assert not menciona_rescate(texto)


# ── Texto fijo ───────────────────────────────────────────────────────────────

def test_texto_cumple_los_cuatro_puntos():
    t = main.texto_persona_encerrada(True, falta_direccion=True)
    assert "¿La persona sigue encerrada o ya pudo salir?" in t          # 1
    assert "4301-3967" in t and "1565024510" in t                       # 2
    assert "WhatsApp no es el medio" in t and "sí o sí" in t            # 3
    assert "dirección exacta" in t                                      # 4
    # El aviso de emergencia no se duplica.
    assert main.asegurar_aviso_emergencia(t) == t


def test_texto_con_direccion_no_pide_direccion():
    t = main.texto_persona_encerrada(True, falta_direccion=False, registrado=True)
    assert "dirección" not in t and main.RECLAMO_URGENTE_AVISADO in t
    t = main.texto_persona_encerrada(True, falta_direccion=False, intento_registro=True)
    assert main.RECLAMO_URGENTE_SIN_AVISO in t


# ── Flujo completo ──────────────────────────────────────────────────────────

@pytest.fixture
def flujo(monkeypatch):
    r = {"grupo": [], "cliente": [], "solicitudes": [], "historial": [], "grupo_ok": True,
         "recordatorios": []}

    async def historial(_telefono):
        return list(r["historial"])

    async def ninguna(*_a, **_k):
        return None

    async def guardar(datos):
        r["solicitudes"].append(datos)
        return 101

    async def no_silenciada(_t):
        return False

    async def enviar(tel, msg):
        r["cliente"].append(msg)
        return "id"

    async def grupo(tel, resumen, proveedor, solicitud_id):
        r["grupo"].append(resumen)
        return "id-grupo" if r["grupo_ok"] else None

    async def llm(*_a, **_k):
        return TEXTO_LLM

    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", ninguna)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", ninguna)
    monkeypatch.setattr(main, "guardar_solicitud", guardar)
    monkeypatch.setattr(main, "notificar_grupo_solicitud", grupo)
    monkeypatch.setattr(main, "guardar_mensaje", ninguna)
    monkeypatch.setattr(main, "silenciar_conversacion", ninguna)
    monkeypatch.setattr(main, "conversacion_silenciada", no_silenciada)
    monkeypatch.setattr(main, "_enviar_registrando", enviar)
    monkeypatch.setattr(main, "buscar_contacto_csv", lambda _t: None)
    monkeypatch.setattr(main, "buscar_cliente_registrado", lambda _t: None)
    monkeypatch.setattr(main, "programar_recordatorio_direccion", lambda t: r["recordatorios"].append(t))
    monkeypatch.setattr(main, "generar_respuesta", llm)
    return r


def _procesar(tel, texto):
    main.limpiar_estado_conversacion(tel)
    asyncio.run(main.procesar_mensaje_cliente(tel, texto))


def test_sin_direccion_pregunta_da_telefonos_y_pide_direccion(flujo):
    _procesar("5491100008001", "Hay una persona encerrada en el ascensor")
    (resp,) = flujo["cliente"]
    assert resp == main.texto_persona_encerrada(True, falta_direccion=True)
    assert TEXTO_LLM not in resp
    assert flujo["grupo"] == [] and flujo["solicitudes"] == []
    assert main.conversaciones_estado["5491100008001"]["emergencia"] == "persona"
    assert flujo["recordatorios"] == ["5491100008001"]


def test_con_direccion_registra_urgente_y_responde_texto_fijo(flujo):
    _procesar("5491100008002", "Thames 2331, hay una persona atrapada en el ascensor")
    (resp,) = flujo["cliente"]
    assert resp == main.texto_persona_encerrada(True, falta_direccion=False, registrado=True)
    assert "URGENTE" in flujo["grupo"][0]


def test_con_direccion_si_falla_el_grupo_no_dice_que_avisamos(flujo):
    flujo["grupo_ok"] = False
    _procesar("5491100008003", "Thames 2331, hay una persona atrapada en el ascensor")
    (resp,) = flujo["cliente"]
    assert main.RECLAMO_URGENTE_AVISADO not in resp
    assert main.RECLAMO_URGENTE_SIN_AVISO in resp


def test_si_ya_la_sacaron_no_pregunta_y_responde_rescate(flujo):
    _procesar("5491100008004", "ayer quedó gente encerrada, ya los sacaron, hoy sigue parado. Thames 2331")
    (resp,) = flujo["cliente"]
    assert resp == main.texto_persona_encerrada(
        True, falta_direccion=False, registrado=True, rescatada=True
    )
    assert resp.startswith(main.PERSONA_RESCATADA)
    assert main.PREGUNTA_PERSONA_ENCERRADA not in resp
    assert "URGENTE" in flujo["grupo"][0]


def test_rescate_sin_direccion_pide_direccion(flujo):
    _procesar("5491100008011", "quedó una señora encerrada, el encargado la sacó")
    (resp,) = flujo["cliente"]
    assert resp == main.texto_persona_encerrada(True, falta_direccion=True, rescatada=True)
    assert "dirección exacta" in resp and main.PREGUNTA_PERSONA_ENCERRADA not in resp
    assert main.conversaciones_estado["5491100008011"]["emergencia"] == "otra"


@pytest.mark.parametrize("texto", [
    "hay una persona encerrada, todavía no la sacaron",
    "mi vieja quedó encerrada y nadie la sacó",
    "quedó encerrado y no pudo salir",
    "¿ya la sacaron? mi mamá estaba encerrada",
    "ya la sacaron pero sigue encerrada la otra señora",
])
def test_en_la_duda_pregunta(flujo, texto):
    _procesar("5491100008012", texto)
    (resp,) = flujo["cliente"]
    assert resp.startswith(main.PREGUNTA_PERSONA_ENCERRADA)
    assert main.PERSONA_RESCATADA not in resp


def test_respuesta_ya_salio_a_la_pregunta_responde_rescate(flujo):
    flujo["historial"] = [
        {"role": "user", "content": "Hay una persona encerrada en el ascensor"},
        {"role": "assistant", "content": main.texto_persona_encerrada(True, falta_direccion=True)},
    ]
    _procesar("5491100008013", "ya salió, es en Thames 2331")
    (resp,) = flujo["cliente"]
    assert resp.startswith(main.PERSONA_RESCATADA) and main.RECLAMO_URGENTE_AVISADO in resp
    assert len(flujo["solicitudes"]) == 1


def test_respuesta_a_la_pregunta_no_repite_la_pregunta(flujo):
    flujo["historial"] = [
        {"role": "user", "content": "Hay una persona encerrada en el ascensor"},
        {"role": "assistant", "content": main.texto_persona_encerrada(True, falta_direccion=True)},
    ]
    _procesar("5491100008005", "sí, sigue encerrada, es en Thames 2331")
    (resp,) = flujo["cliente"]
    assert main.PREGUNTA_PERSONA_ENCERRADA not in resp
    assert main.LLAMAR_PERSONA_ENCERRADA in resp and main.RECLAMO_URGENTE_AVISADO in resp
    assert "URGENTE" in flujo["grupo"][0]


def test_si_solo_manda_la_direccion_sigue_el_texto_fijo(flujo):
    flujo["historial"] = [
        {"role": "user", "content": "Hay una persona encerrada en el ascensor"},
        {"role": "assistant", "content": main.texto_persona_encerrada(True, falta_direccion=True)},
    ]
    _procesar("5491100008006", "Thames 2331")
    (resp,) = flujo["cliente"]
    assert main.LLAMAR_PERSONA_ENCERRADA in resp and TEXTO_LLM not in resp
    assert len(flujo["solicitudes"]) == 1


def test_derivar_admin_del_llm_no_silencia_una_persona_encerrada(flujo, monkeypatch):
    async def llm(*_a, **_k):
        return "Lo paso a administración. [DERIVAR_ADMIN]"

    monkeypatch.setattr(main, "generar_respuesta", llm)
    _procesar("5491100008007", "Thames 2331, hay una persona encerrada")
    assert main.obtener_estado_conversacion("5491100008007") != "pendiente_admin"
    assert "administración" not in flujo["cliente"][0]


@pytest.mark.parametrize("texto", ["sale humo del motor", "el ascensor quedó entre pisos"])
def test_emergencia_sin_persona_no_pregunta_por_encerrados(flujo, texto):
    _procesar("5491100008008", texto)
    (resp,) = flujo["cliente"]
    assert resp == main.PEDIDO_DIRECCION_EMERGENCIA
    assert "encerrad" not in resp.lower()
    assert main.conversaciones_estado["5491100008008"]["emergencia"] == "otra"


def test_reclamo_comun_no_pregunta_por_encerrados(flujo):
    _procesar("5491100008009", "Thames 2331, el ascensor está parado")
    assert flujo["cliente"] == ["Perfecto, el reclamo quedó registrado."]


# ── Recordatorio de dirección ───────────────────────────────────────────────

@pytest.mark.parametrize("emergencia, esperado", [
    ("persona", "RECORDATORIO_DIRECCION_PERSONA_ENCERRADA"),
    ("otra", "RECORDATORIO_DIRECCION_EMERGENCIA"),
    (None, "RECORDATORIO_DIRECCION"),
])
def test_recordatorio_repite_telefonos_en_emergencia(monkeypatch, emergencia, esperado):
    enviados = []

    async def enviar(tel, msg):
        enviados.append(msg)

    async def ninguna(*_a, **_k):
        return None

    async def no_silenciada(_t):
        return False

    monkeypatch.setattr(main, "RECORDATORIO_DIRECCION_SEGUNDOS", 0)
    monkeypatch.setattr(main, "olivia_debe_atender", lambda: True)
    monkeypatch.setattr(main, "conversacion_silenciada", no_silenciada)
    monkeypatch.setattr(main, "guardar_mensaje", ninguna)
    monkeypatch.setattr(main, "_enviar_registrando", enviar)
    main.marcar_estado_conversacion("5491100008010", "pendiente_direccion", emergencia=emergencia)
    asyncio.run(main._recordar_direccion("5491100008010"))

    (msg,) = enviados
    assert msg == getattr(main, esperado)
    assert "dirección exacta" in msg
    if emergencia:
        assert "4301-3967" in msg and "1565024510" in msg
    assert ("encerrad" in msg.lower()) == (emergencia == "persona")
