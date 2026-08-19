import asyncio

from agent.grupos import es_destino_grupo
from agent.providers.whapi import ProveedorWhapi
from agent.providers.zapi import ProveedorZapi
from agent.tools import notificar_grupo_solicitud


class _ClienteHttpProhibido:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("No debe abrirse una conexión HTTP para un destino grupal")


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


def test_notificar_grupo_solicitud_envia_por_el_canal_dedicado_y_vincula_mensaje(monkeypatch):
    llamados = []

    class _ProveedorGrupoEspia:
        async def enviar_mensaje_grupo(self, mensaje):
            llamados.append(mensaje)
            return "mensaje-grupo-101"

    vinculado = {}

    async def actualizar_falso(solicitud_id, mensaje_grupo_id):
        vinculado["solicitud_id"] = solicitud_id
        vinculado["mensaje_grupo_id"] = mensaje_grupo_id

    monkeypatch.setattr("agent.memory.actualizar_mensaje_grupo_id", actualizar_falso)

    resultado = asyncio.run(
        notificar_grupo_solicitud(
            "5491100000000",
            "Ugarteche 3050. Ascensor parado.",
            _ProveedorGrupoEspia(),
            123,
        )
    )

    assert resultado == "mensaje-grupo-101"
    assert llamados == ["#123 — Ugarteche 3050. Ascensor parado."]
    assert vinculado == {"solicitud_id": 123, "mensaje_grupo_id": "mensaje-grupo-101"}


def test_notificar_grupo_solicitud_actualizacion_no_duplica_el_id(monkeypatch):
    """El llamador (main.py) pasa el resumen de la ampliación SIN el '#N' — este
    test confirma que el texto final que llega a enviar_mensaje_grupo() tiene el
    ID una sola vez, sin importar qué texto arme el llamador."""
    llamados = []

    class _ProveedorGrupoEspia:
        async def enviar_mensaje_grupo(self, mensaje):
            llamados.append(mensaje)
            return "mensaje-grupo-102"

    async def actualizar_falso(*_args, **_kwargs):
        return None

    monkeypatch.setattr("agent.memory.actualizar_mensaje_grupo_id", actualizar_falso)

    asyncio.run(
        notificar_grupo_solicitud(
            "5491100000000",
            "Actualización: Ascensores de familia 1, 3, 4 y 6.",
            _ProveedorGrupoEspia(),
            77,
        )
    )

    assert llamados == ["#77 — Actualización: Ascensores de familia 1, 3, 4 y 6."]
    assert llamados[0].count("#77") == 1


def test_notificar_grupo_solicitud_falla_zapi_devuelve_none_y_loguea(monkeypatch, caplog):
    class _ProveedorGrupoFalla:
        async def enviar_mensaje_grupo(self, _mensaje):
            return None

    with caplog.at_level("ERROR", logger="agentkit"):
        resultado = asyncio.run(
            notificar_grupo_solicitud(
                "5491100000000",
                "Ugarteche 3050. Ascensor parado.",
                _ProveedorGrupoFalla(),
                123,
            )
        )

    assert resultado is None
    assert any("ERROR ENVÍO GRUPO" in registro.message for registro in caplog.records)


def test_notificar_grupo_solicitud_sin_proveedor_no_falla():
    resultado = asyncio.run(
        notificar_grupo_solicitud("5491100000000", "Ugarteche 3050.", None, 123)
    )
    assert resultado is None


def test_zapi_enviar_mensaje_grupo_usa_el_canal_dedicado(monkeypatch):
    monkeypatch.setenv("WHAPI_GROUP_ID", "120363000000000000@g.us")
    monkeypatch.setenv("ZAPI_INSTANCE_ID", "instancia")
    monkeypatch.setenv("ZAPI_TOKEN", "token")

    llamadas = []

    class _RespuestaFalsa:
        status_code = 200

        def json(self):
            return {"messageId": "msg-grupo-1"}

    class _ClienteHttpEspia:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json, headers):
            llamadas.append((url, json))
            return _RespuestaFalsa()

    monkeypatch.setattr("agent.providers.zapi.httpx.AsyncClient", _ClienteHttpEspia)
    proveedor = ProveedorZapi()

    resultado = asyncio.run(proveedor.enviar_mensaje_grupo("Reclamo #1 — Ugarteche 3050"))

    assert resultado == "msg-grupo-1"
    assert len(llamadas) == 1
    assert llamadas[0][1]["phone"] == "120363000000000000-group"
    assert llamadas[0][1]["message"] == "Reclamo #1 — Ugarteche 3050"


def test_zapi_enviar_mensaje_grupo_sin_group_id_no_falla(monkeypatch):
    monkeypatch.delenv("WHAPI_GROUP_ID", raising=False)
    proveedor = ProveedorZapi()

    resultado = asyncio.run(proveedor.enviar_mensaje_grupo("Reclamo #1"))

    assert resultado is None
