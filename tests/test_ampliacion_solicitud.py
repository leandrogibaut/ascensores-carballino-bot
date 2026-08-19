import asyncio
import os

os.environ.setdefault("OLLAMA_API_KEY", "prueba-local")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/prueba-ampliacion.db")

from agent.memory import (  # noqa: E402
    inicializar_db,
    guardar_solicitud,
    agregar_ampliacion_solicitud,
    obtener_solicitud_pendiente_reciente_por_telefono,
    actualizar_estado_solicitud,
)


def test_agregar_ampliacion_anexa_sin_perder_el_texto_original():
    asyncio.run(inicializar_db())
    solicitud_id = asyncio.run(guardar_solicitud({
        "telefono_cliente": "5491100001111",
        "tipo": "4 ascensores fuera de servicio por corte de luz",
        "direccion": "Ugarteche 3050",
        "quien_abre": "Disponibilidad no informada",
    }))

    actualizado = asyncio.run(
        agregar_ampliacion_solicitud(solicitud_id, "Ascensores de familia 1, 3, 4 y 6")
    )

    assert "4 ascensores fuera de servicio" in actualizado.tipo
    assert "Ascensores de familia 1, 3, 4 y 6" in actualizado.tipo


def test_agregar_ampliacion_no_duplica_si_el_texto_ya_esta():
    asyncio.run(inicializar_db())
    solicitud_id = asyncio.run(guardar_solicitud({
        "telefono_cliente": "5491100001112",
        "tipo": "Ascensor parado",
        "direccion": "Ugarteche 3050",
    }))

    asyncio.run(agregar_ampliacion_solicitud(solicitud_id, "Cabina 4 con agua"))
    actualizado = asyncio.run(agregar_ampliacion_solicitud(solicitud_id, "Cabina 4 con agua"))

    assert actualizado.tipo.count("Cabina 4 con agua") == 1


def test_agregar_ampliacion_solicitud_inexistente_retorna_none():
    asyncio.run(inicializar_db())
    resultado = asyncio.run(agregar_ampliacion_solicitud(999999, "Texto cualquiera"))
    assert resultado is None


def test_obtener_solicitud_pendiente_reciente_ignora_resueltas():
    asyncio.run(inicializar_db())
    telefono = "5491100002222"
    id_resuelta = asyncio.run(guardar_solicitud({
        "telefono_cliente": telefono, "tipo": "Botonera falla", "direccion": "Ugarteche 3050",
    }))
    asyncio.run(actualizar_estado_solicitud(id_resuelta, "resuelto", "Cambiada"))
    id_pendiente = asyncio.run(guardar_solicitud({
        "telefono_cliente": telefono, "tipo": "Ascensor parado", "direccion": "Ugarteche 3050",
    }))

    encontrada = asyncio.run(obtener_solicitud_pendiente_reciente_por_telefono(telefono))

    assert encontrada.id == id_pendiente


def test_obtener_solicitud_pendiente_reciente_incluye_pendiente_con_nota():
    asyncio.run(inicializar_db())
    telefono = "5491100002233"
    solicitud_id = asyncio.run(guardar_solicitud({
        "telefono_cliente": telefono, "tipo": "Ascensor parado", "direccion": "Ugarteche 3050",
    }))
    asyncio.run(actualizar_estado_solicitud(solicitud_id, "pendiente_con_nota", "Falta repuesto"))

    encontrada = asyncio.run(obtener_solicitud_pendiente_reciente_por_telefono(telefono))

    assert encontrada.id == solicitud_id


def test_obtener_solicitud_pendiente_reciente_telefono_sin_solicitudes_retorna_none():
    asyncio.run(inicializar_db())
    encontrada = asyncio.run(
        obtener_solicitud_pendiente_reciente_por_telefono("5491100009999999")
    )
    assert encontrada is None
