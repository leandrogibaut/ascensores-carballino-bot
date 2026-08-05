import asyncio

from agent.grupos import es_destino_grupo
from agent.providers.whapi import ProveedorWhapi
from agent.providers.zapi import ProveedorZapi
from agent.tools import notificar_grupo_solicitud


class _ClienteHttpProhibido:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("No debe abrirse una conexión HTTP para un destino grupal")


class _ProveedorEspia:
    async def enviar_mensaje(self, *_args, **_kwargs):
        raise AssertionError("La función antigua no debe llamar al proveedor")


class _RequestFalso:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_detecta_formatos_de_grupo_y_no_confunde_un_privado(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363000000000000@g.us")

    assert es_destino_grupo("120363000000000000@g.us")
    assert es_destino_grupo("120363000000000000-group")
    assert es_destino_grupo("120363000000000000")
    assert not es_destino_grupo("5491131815195")


def test_zapi_sigue_leyendo_el_grupo_interno(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363000000000000@g.us")
    proveedor = ProveedorZapi()
    request = _RequestFalso({
        "type": "ReceivedCallback",
        "phone": "120363000000000000-group",
        "isGroup": True,
        "fromMe": False,
        "fromApi": False,
        "messageId": "mensaje-tecnico-1",
        "senderName": "Técnico",
        "text": {"message": "Listo"},
    })

    mensajes = asyncio.run(proveedor.parsear_webhook(request))

    assert len(mensajes) == 1
    assert mensajes[0].texto == "Listo"
    assert mensajes[0].telefono.endswith("-group")


def test_zapi_bloquea_texto_y_botones_a_grupos_sin_hacer_http(monkeypatch):
    monkeypatch.setenv("ZAPI_INSTANCE_ID", "instancia")
    monkeypatch.setenv("ZAPI_TOKEN", "token")
    monkeypatch.setattr("agent.providers.zapi.httpx.AsyncClient", _ClienteHttpProhibido)
    proveedor = ProveedorZapi()

    resultado_texto = asyncio.run(
        proveedor.enviar_mensaje("120363000000000000-group", "Factura inesperada")
    )
    resultado_botones = asyncio.run(
        proveedor.enviar_menu_botones(
            "120363000000000000-group",
            "No debe salir",
            [{"id": "1", "label": "Aceptar"}],
        )
    )

    assert resultado_texto is None
    assert resultado_botones is False


def test_whapi_bloquea_cualquier_texto_a_grupos_sin_hacer_http(monkeypatch):
    monkeypatch.setenv("WHAPI_TOKEN", "token")
    monkeypatch.setattr("agent.providers.whapi.httpx.AsyncClient", _ClienteHttpProhibido)
    proveedor = ProveedorWhapi()

    resultado = asyncio.run(
        proveedor.enviar_mensaje("120363000000000000@g.us", "Factura inesperada")
    )

    assert resultado is False


def test_funcion_legacy_de_notificacion_es_un_no_op_permanente():
    resultado = asyncio.run(
        notificar_grupo_solicitud(
            "5491100000000",
            "Ugarteche 3050. Ascensor parado.",
            _ProveedorEspia(),
            123,
        )
    )

    assert resultado is False
