import asyncio
import os

import pytest


os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("WHATSAPP_PROVIDER", "zapi")
os.environ.setdefault("ZAPI_INSTANCE_ID", "prueba")
os.environ.setdefault("ZAPI_TOKEN", "prueba")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-tests.db")

from agent import main  # noqa: E402


@pytest.fixture
def flujo_aislado(monkeypatch):
    enviados_grupo = []
    enviados_cliente = []
    solicitudes = []

    async def historial(_telefono):
        return []

    async def sin_solicitud(_telefono):
        return None

    async def guardar(datos):
        solicitudes.append(datos)
        return 101

    async def grupo(telefono, resumen, proveedor, solicitud_id):
        enviados_grupo.append((telefono, resumen, solicitud_id))
        return "mensaje-grupo-101"

    async def no_op(*_args, **_kwargs):
        return None

    async def no_silenciada(_telefono):
        return False

    async def enviar_cliente(telefono, mensaje):
        enviados_cliente.append((telefono, mensaje))
        return "mensaje-cliente"

    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", sin_solicitud)
    monkeypatch.setattr(main, "guardar_solicitud", guardar)
    monkeypatch.setattr(main, "notificar_grupo_solicitud", grupo)
    monkeypatch.setattr(main, "guardar_mensaje", no_op)
    monkeypatch.setattr(main, "silenciar_conversacion", no_op)
    monkeypatch.setattr(main, "conversacion_silenciada", no_silenciada)
    monkeypatch.setattr(main, "_enviar_registrando", enviar_cliente)
    monkeypatch.setattr(main, "buscar_contacto_csv", lambda _telefono: None)
    monkeypatch.setattr(main, "buscar_cliente_registrado", lambda _telefono: None)

    return enviados_grupo, enviados_cliente, solicitudes


def test_emergencia_avisa_grupo_y_mantiene_respuesta_de_llamada(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Llamá ahora por teléfono común al 4301-3967 o al 1565024510. No llamada de WhatsApp."

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100000116",
            "Soy de Thames 2331. Hay una persona atrapada en un ascensor",
        )
    )

    assert len(grupos) == 1
    assert "URGENTE" in grupos[0][1]
    assert solicitudes[0]["direccion"] == "Thames 2331"
    assert clientes[0][1].startswith("Llamá ahora")


def test_libertad_se_deriva_aunque_modelo_pida_quien_abre(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. ¿Quién abre o en qué horario pueden recibir al técnico?"

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100004507",
            "Mi nombre es Daniel, soy el encargado de Libertad 1262. El ascensor no abre la puerta del 2° piso.",
        )
    )

    assert len(grupos) == 1
    assert solicitudes[0]["direccion"] == "Libertad 1262"
    assert solicitudes[0]["quien_abre"] == "Daniel, encargado"
    assert "ya le enviamos" in clientes[0][1].lower()


def test_torre_c_se_envia_como_arribenos_montaneses(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. Decime la dirección y quién abre."

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, _clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100008417",
            "El ascensor 4/5 de Torre C tiene movimientos raros",
        )
    )

    assert len(grupos) == 1
    assert "Arribeños / Montañeses 3150 Torre C" in grupos[0][1]
    assert solicitudes[0]["direccion"] == "Montañeses 3150 Torre C"
    assert solicitudes[0]["quien_abre"] == "Disponibilidad no informada"


def test_sin_direccion_pide_dato_y_no_inventa_envio(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. ¿Cuál es la dirección del edificio?"

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100009999",
            "El ascensor está parado y hace movimientos raros",
        )
    )

    assert grupos == []
    assert solicitudes == []
    assert "dirección" in clientes[0][1].lower()


def test_respuesta_posterior_con_solo_direccion_completa_el_reclamo(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. Ya lo paso a los técnicos."

    async def historial(_telefono):
        return [
            {"role": "user", "content": "El ascensor está parado y hace movimientos raros"},
            {"role": "assistant", "content": "¿Cuál es la dirección del edificio?"},
        ]

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    grupos, _clientes, solicitudes = flujo_aislado

    asyncio.run(main.procesar_mensaje_cliente("5491100009998", "Libertad 1262"))

    assert len(grupos) == 1
    assert solicitudes[0]["direccion"] == "Libertad 1262"
