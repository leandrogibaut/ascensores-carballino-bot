import asyncio
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Text


os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("WHATSAPP_PROVIDER", "zapi")
os.environ.setdefault("ZAPI_INSTANCE_ID", "prueba")
os.environ.setdefault("ZAPI_TOKEN", "prueba")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-tests.db")

from agent import main  # noqa: E402
from agent.memory import Solicitud  # noqa: E402


def test_descripcion_de_solicitud_admite_mas_de_50_caracteres():
    assert isinstance(Solicitud.__table__.c.tipo.type, Text)


@pytest.fixture
def flujo_aislado(monkeypatch):
    enviados_grupo = []
    enviados_cliente = []
    solicitudes = []

    async def historial(_telefono):
        return []

    async def sin_solicitud(_telefono):
        return None

    async def sin_solicitud_pendiente(_telefono, horas=24):
        return None

    async def guardar(datos):
        solicitudes.append(datos)
        return 101

    async def no_op(*_args, **_kwargs):
        return None

    async def no_silenciada(_telefono):
        return False

    async def enviar_cliente(telefono, mensaje):
        enviados_cliente.append((telefono, mensaje))
        return "mensaje-cliente"

    async def grupo(telefono, resumen, proveedor, solicitud_id):
        enviados_grupo.append((telefono, resumen, solicitud_id))
        return "mensaje-grupo-101"

    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", sin_solicitud)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", sin_solicitud_pendiente)
    monkeypatch.setattr(main, "guardar_solicitud", guardar)
    monkeypatch.setattr(main, "notificar_grupo_solicitud", grupo)
    monkeypatch.setattr(main, "guardar_mensaje", no_op)
    monkeypatch.setattr(main, "silenciar_conversacion", no_op)
    monkeypatch.setattr(main, "conversacion_silenciada", no_silenciada)
    monkeypatch.setattr(main, "_enviar_registrando", enviar_cliente)
    monkeypatch.setattr(main, "buscar_contacto_csv", lambda _telefono: None)
    monkeypatch.setattr(main, "buscar_cliente_registrado", lambda _telefono: None)
    monkeypatch.setattr(main, "programar_recordatorio_direccion", lambda _telefono: None)

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


def test_libertad_se_registra_aunque_modelo_pida_quien_abre(monkeypatch, flujo_aislado):
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
    assert "registrado" in clientes[0][1].lower()


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
        return "Buen día. El reclamo quedó registrado."

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


def test_tag_del_modelo_sin_direccion_no_pasa_al_grupo(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return (
            "Listo, gracias. "
            "[SOLICITUD_COMPLETA: Hotel Alcázar Fabián. Ascensor detenido. Disponibilidad no informada.]"
        )

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100007777",
            "Se quedó el ascensor entre dos pisos y ahora no funciona",
        )
    )

    assert grupos == []
    assert solicitudes == []
    assert "dirección exacta" in clientes[0][1].lower()
    assert "no voy a poder pasar" not in clientes[0][1].lower()


def test_insiste_si_responden_sin_direccion(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Listo, gracias."

    async def historial(_telefono):
        return [
            {"role": "user", "content": "El ascensor quedó parado"},
            {"role": "assistant", "content": main.PEDIDO_DIRECCION},
        ]

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(main.procesar_mensaje_cliente("5491100007778", "No sé, averiguo"))

    assert grupos == []
    assert solicitudes == []
    assert "no voy a poder registrar" in clientes[0][1].lower()


def test_audio_se_interpreta_sin_devolver_transcripcion(monkeypatch, flujo_aislado):
    recibido_modelo = []

    async def responder(mensaje, *_args, **_kwargs):
        recibido_modelo.append(mensaje)
        return "Entendido, la carcasa de acero inoxidable alrededor del techo..."

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100007779",
            "El ascensor no funciona y hace ruido",
            es_audio=True,
        )
    )

    assert "No repitas" in recibido_modelo[0]
    assert grupos == []
    assert solicitudes == []
    assert clientes[0][1].startswith("Para poder registrar")
    assert "carcasa" not in clientes[0][1].lower()


def test_reclamo_con_direccion_no_agrega_aviso_de_emergencia(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return (
            "Perfecto. "
            "[SOLICITUD_COMPLETA: Ugarteche 3050. Ascensor parado. Disponibilidad no informada.]"
        )

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100007780",
            "Ugarteche 3050 ascensor parado",
        )
    )

    assert len(grupos) == 1
    assert "4301-3967" not in clientes[0][1]
    assert clientes[0][1] == "Perfecto, el reclamo quedó registrado."


def test_emergencia_sin_direccion_prioriza_llamada_y_no_deriva(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Llamá ahora por teléfono común."

    monkeypatch.setattr(main, "generar_respuesta", responder)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100007781",
            "Hay una persona encerrada dentro del ascensor",
        )
    )

    assert grupos == []
    assert solicitudes == []
    assert clientes[0][1].startswith("Llamá ahora")
    assert "dirección exacta" in clientes[0][1].lower()


def test_si_notificar_grupo_falla_no_dice_que_quedo_registrado(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. Decime quién abre."

    async def grupo_falla(_telefono, _resumen, _proveedor, _solicitud_id):
        return None

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "notificar_grupo_solicitud", grupo_falla)
    _grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100009111",
            "Libertad 1262. El ascensor no funciona. Soy Daniel, encargado.",
        )
    )

    assert solicitudes[0]["direccion"] == "Libertad 1262"
    assert "quedó registrado" not in clientes[0][1]
    assert "4301-3967" in clientes[0][1]


def test_ampliacion_misma_direccion_anexa_y_notifica_actualizacion(monkeypatch, flujo_aislado):
    solicitud_previa = Solicitud(
        id=77,
        telefono_cliente="5491100008888",
        tipo="4 ascensores fuera de servicio por corte de luz",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-77",
    )

    async def historial(_telefono):
        return [
            {"role": "user", "content": "Ugarteche 3050. Corte de luz, 4 ascensores fuera de servicio."},
            {"role": "assistant", "content": "Perfecto, el reclamo quedó registrado."},
        ]

    async def responder(*_args, **_kwargs):
        return (
            "Gracias por la info.\n"
            "[SOLICITUD_COMPLETA: Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.]"
        )

    async def solicitud_activa(_telefono):
        return solicitud_previa

    ampliaciones = []

    async def ampliacion(solicitud_id, texto_adicional):
        ampliaciones.append((solicitud_id, texto_adicional))
        return solicitud_previa

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", solicitud_activa)
    monkeypatch.setattr(main, "agregar_ampliacion_solicitud", ampliacion)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100008888", "Ascensores de familia 1, 3, 4 y 6"
        )
    )

    assert solicitudes == []  # no crea una solicitud nueva
    assert ampliaciones == [(77, "Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.")]
    assert len(grupos) == 1
    assert grupos[0][2] == 77  # notifica sobre la solicitud existente, no una nueva
    assert "Ascensores de familia 1, 3, 4 y 6" in grupos[0][1]
    # El texto pasado a notificar_grupo_solicitud() NO debe incluir "#77" —
    # ese prefijo lo antepone tools.py una sola vez con el solicitud_id.
    assert "#77" not in grupos[0][1]
    assert clientes[0][1] == "Perfecto, el reclamo quedó registrado."


def test_ampliacion_con_fallo_de_grupo_no_confirma_al_cliente(monkeypatch, flujo_aislado):
    solicitud_previa = Solicitud(
        id=77,
        telefono_cliente="5491100008890",
        tipo="4 ascensores fuera de servicio por corte de luz",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-77",
    )

    async def historial(_telefono):
        return [
            {"role": "user", "content": "Ugarteche 3050. Corte de luz, 4 ascensores fuera de servicio."},
            {"role": "assistant", "content": "Perfecto, el reclamo quedó registrado."},
        ]

    async def responder(*_args, **_kwargs):
        return (
            "Gracias por la info.\n"
            "[SOLICITUD_COMPLETA: Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.]"
        )

    async def solicitud_activa(_telefono):
        return solicitud_previa

    ampliaciones = []

    async def ampliacion(solicitud_id, texto_adicional):
        ampliaciones.append((solicitud_id, texto_adicional))
        return solicitud_previa

    async def grupo_falla(_telefono, _resumen, _proveedor, _solicitud_id):
        return None

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", solicitud_activa)
    monkeypatch.setattr(main, "agregar_ampliacion_solicitud", ampliacion)
    monkeypatch.setattr(main, "notificar_grupo_solicitud", grupo_falla)
    _grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100008890", "Ascensores de familia 1, 3, 4 y 6"
        )
    )

    # No crea una solicitud nueva.
    assert solicitudes == []
    # La ampliación queda anexada de todas formas (no se pierde información).
    assert ampliaciones == [(77, "Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.")]
    # El cliente NO recibe confirmación positiva.
    assert clientes[0][1] != "Perfecto, el reclamo quedó registrado."
    assert "quedó registrado" not in clientes[0][1]
    assert clientes[0][1] == main.MENSAJE_RECLAMO_SIN_GRUPO


def test_ampliacion_sin_direccion_propia_se_anexa_a_la_existente(monkeypatch, flujo_aislado):
    solicitud_previa = Solicitud(
        id=88,
        telefono_cliente="5491100008899",
        tipo="Ascensor parado",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-88",
    )

    async def historial(_telefono):
        return [
            {"role": "user", "content": "Ugarteche 3050 ascensor parado"},
            {"role": "assistant", "content": "Perfecto, el reclamo quedó registrado."},
        ]

    async def responder(*_args, **_kwargs):
        return "[SOLICITUD_COMPLETA: Cabina 4 con agua, no pude chequear las bombas. Guardia 24hs.]"

    async def solicitud_activa(_telefono):
        return solicitud_previa

    ampliaciones = []

    async def ampliacion(solicitud_id, texto_adicional):
        ampliaciones.append((solicitud_id, texto_adicional))
        return solicitud_previa

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", solicitud_activa)
    monkeypatch.setattr(main, "agregar_ampliacion_solicitud", ampliacion)
    grupos, _clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100008899", "Y tengo la cabina 4 con agua, no pude chequear las bombas"
        )
    )

    assert solicitudes == []
    assert ampliaciones == [(88, "Cabina 4 con agua, no pude chequear las bombas. Guardia 24hs.")]
    assert len(grupos) == 1
    assert grupos[0][2] == 88


def test_direccion_distinta_dentro_de_10_min_crea_reclamo_nuevo_no_ampliacion(monkeypatch, flujo_aislado):
    """Un contacto multi-dirección puede tener dos reclamos distintos en pocos minutos:
    no deben fusionarse en la misma solicitud solo por compartir teléfono."""
    solicitud_previa = Solicitud(
        id=99,
        telefono_cliente="5491100008900",
        tipo="Ascensor parado",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-99",
    )

    async def responder(*_args, **_kwargs):
        return (
            "Perfecto. "
            "[SOLICITUD_COMPLETA: Balbín 2421. Botonera trabada. Disponibilidad no informada.]"
        )

    async def solicitud_activa(_telefono):
        return solicitud_previa

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", solicitud_activa)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100008900", "Balbín 2421 botonera trabada"
        )
    )

    assert len(solicitudes) == 1
    assert solicitudes[0]["direccion"] == "Balbín 2421"
    assert len(grupos) == 1
    assert grupos[0][2] != 99  # notifica una solicitud NUEVA, no la #99 de Ugarteche
    assert clientes[0][1] == "Perfecto, el reclamo quedó registrado."


def test_no_activa_silencio_automatico_tras_confirmar(monkeypatch, flujo_aislado):
    silenciado = []

    async def marcar_silencio(*args, **kwargs):
        silenciado.append((args, kwargs))

    async def responder(*_args, **_kwargs):
        return (
            "Perfecto. "
            "[SOLICITUD_COMPLETA: Ugarteche 3050. Ascensor parado. Disponibilidad no informada.]"
        )

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "silenciar_conversacion", marcar_silencio)
    _grupos, _clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100008901", "Ugarteche 3050 ascensor parado")
    )

    assert silenciado == []


def test_seguimiento_con_solicitud_pendiente_reciente_no_pide_falla_ni_direccion(monkeypatch, flujo_aislado):
    solicitud_pendiente = Solicitud(
        id=55,
        telefono_cliente="5491100009201",
        tipo="4 ascensores fuera de servicio por corte de luz",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-55",
        timestamp=datetime.utcnow() - timedelta(hours=1),
    )

    contexto_recibido = {}

    async def responder(_mensaje, _historial, contexto_cliente=""):
        contexto_recibido["valor"] = contexto_cliente
        return "Sí, tengo registrado el reclamo de Ugarteche 3050. Todavía figura pendiente."

    async def solicitud_pendiente_reciente(_telefono, horas=24):
        return solicitud_pendiente

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", solicitud_pendiente_reciente)
    _grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100009201", "Buenas me confirmas si van a venir??")
    )

    contexto = contexto_recibido["valor"]
    assert "#55" in contexto
    assert "Ugarteche 3050" in contexto
    assert "4 ascensores fuera de servicio por corte de luz" in contexto
    assert "pendiente" in contexto.lower()
    assert "¿Cuál es la falla" not in clientes[0][1]
    assert "dirección" not in clientes[0][1].lower()


def test_seguimiento_al_dia_siguiente_dentro_de_24hs_reconoce_reclamo(monkeypatch, flujo_aislado):
    solicitud_pendiente = Solicitud(
        id=60,
        telefono_cliente="5491100009202",
        tipo="Ascensor parado",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-60",
        timestamp=datetime.utcnow() - timedelta(hours=20),
    )

    contexto_recibido = {}

    async def responder(_mensaje, _historial, contexto_cliente=""):
        contexto_recibido["valor"] = contexto_cliente
        return "Sí, sigue registrado y pendiente. En cuanto tengamos novedades te avisamos."

    # El historial de mensajes ya venció (timeout de 4hs en obtener_historial),
    # así que llega vacío — la única fuente de contexto es la solicitud pendiente.
    async def historial_vacio(_telefono):
        return []

    async def solicitud_pendiente_reciente(_telefono, horas=24):
        return solicitud_pendiente

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial_vacio)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", solicitud_pendiente_reciente)
    _grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100009202", "No vieron los msjs de ayer??")
    )

    assert "#60" in contexto_recibido["valor"]
    assert "¿Cuál es la falla" not in clientes[0][1]


def test_seguimiento_sin_solicitud_pendiente_sigue_flujo_normal(monkeypatch, flujo_aislado):
    async def responder(*_args, **_kwargs):
        return "Buen día. ¿Cuál es la falla técnica o reclamo?"

    monkeypatch.setattr(main, "generar_respuesta", responder)
    # La fixture ya monkeypatchea obtener_solicitud_pendiente_reciente_por_telefono
    # para devolver None por default — no hace falta pisarlo de nuevo acá.
    _grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100009203", "¿Hay novedades?")
    )

    assert clientes[0][1] == "Buen día. ¿Cuál es la falla técnica o reclamo?"


def test_seguimiento_no_trata_solicitud_resuelta_como_pendiente(monkeypatch, flujo_aislado):
    # obtener_solicitud_pendiente_reciente_por_telefono ya filtra por estado en
    # memory.py (Task 3) — acá confirmamos que si esa consulta correctamente no
    # devuelve nada (solicitud resuelta), main.py no inyecta contexto de seguimiento.
    async def sin_pendiente(_telefono, horas=24):
        return None

    async def responder(*_args, **_kwargs):
        return "Buen día. ¿Cuál es la falla técnica o reclamo?"

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", sin_pendiente)
    _grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100009204", "¿Ya lo pasaron?")
    )

    assert clientes[0][1] == "Buen día. ¿Cuál es la falla técnica o reclamo?"


def test_seguimiento_no_inventa_tecnico_en_camino(monkeypatch, flujo_aislado):
    solicitud_pendiente = Solicitud(
        id=61,
        telefono_cliente="5491100009205",
        tipo="Ascensor parado",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-61",
        timestamp=datetime.utcnow() - timedelta(hours=2),
    )

    contexto_recibido = {}

    async def responder(_mensaje, _historial, contexto_cliente=""):
        contexto_recibido["valor"] = contexto_cliente
        return "Sí, tengo registrado el reclamo de Ugarteche 3050. Todavía figura pendiente."

    async def solicitud_pendiente_reciente(_telefono, horas=24):
        return solicitud_pendiente

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", solicitud_pendiente_reciente)
    _grupos, clientes, _solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente("5491100009205", "¿Cuándo vienen?")
    )

    contexto = contexto_recibido["valor"].lower()
    # El contexto instruye explícitamente a no inventar (la instrucción negativa
    # necesariamente menciona la frase prohibida al negarla).
    assert "no inventes" in contexto
    assert "prometas" in contexto
    # Lo que importa es que la respuesta real al cliente no invente nada:
    respuesta_cliente = clientes[0][1].lower()
    assert "en camino" not in respuesta_cliente
    assert "horario" not in respuesta_cliente


def test_seguimiento_no_altera_ampliacion_de_10_min(monkeypatch, flujo_aislado):
    """Si dentro de los 10 min hay una solicitud activa Y además una solicitud
    pendiente reciente (24hs) para el mismo teléfono (puede ser la misma), un
    mensaje de AMPLIACIÓN real (no un seguimiento) debe seguir tomando la rama
    de ampliación del Task 5 sin verse afectado por la lógica de seguimiento."""
    solicitud_previa = Solicitud(
        id=77,
        telefono_cliente="5491100009206",
        tipo="4 ascensores fuera de servicio por corte de luz",
        direccion="Ugarteche 3050",
        quien_abre="Guardia 24hs",
        estado="pendiente",
        mensaje_grupo_id="mensaje-grupo-77",
        timestamp=datetime.utcnow(),
    )

    async def historial(_telefono):
        return [
            {"role": "user", "content": "Ugarteche 3050. Corte de luz, 4 ascensores fuera de servicio."},
            {"role": "assistant", "content": "Perfecto, el reclamo quedó registrado."},
        ]

    async def responder(*_args, **_kwargs):
        return (
            "Gracias por la info.\n"
            "[SOLICITUD_COMPLETA: Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.]"
        )

    async def solicitud_activa(_telefono):
        return solicitud_previa

    async def solicitud_pendiente_reciente(_telefono, horas=24):
        return solicitud_previa

    ampliaciones = []

    async def ampliacion(solicitud_id, texto_adicional):
        ampliaciones.append((solicitud_id, texto_adicional))
        return solicitud_previa

    monkeypatch.setattr(main, "generar_respuesta", responder)
    monkeypatch.setattr(main, "obtener_historial", historial)
    monkeypatch.setattr(main, "obtener_solicitud_activa_por_telefono", solicitud_activa)
    monkeypatch.setattr(main, "obtener_solicitud_pendiente_reciente_por_telefono", solicitud_pendiente_reciente)
    monkeypatch.setattr(main, "agregar_ampliacion_solicitud", ampliacion)
    grupos, clientes, solicitudes = flujo_aislado

    asyncio.run(
        main.procesar_mensaje_cliente(
            "5491100009206", "Ascensores de familia 1, 3, 4 y 6"
        )
    )

    assert solicitudes == []
    assert ampliaciones == [(77, "Ugarteche 3050. Ascensores de familia 1, 3, 4 y 6. Guardia 24hs.")]
    assert len(grupos) == 1
    assert grupos[0][2] == 77
    assert clientes[0][1] == "Perfecto, el reclamo quedó registrado."
