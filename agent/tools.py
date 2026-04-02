# agent/tools.py — Herramientas del agente
# Generado por AgentKit

"""
Herramientas específicas de Ascensores Carballino.
Permiten al agente gestionar solicitudes de servicio y mantenimiento.
"""

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_contactos() -> dict:
    """Retorna los datos de contacto de la empresa."""
    info = cargar_info_negocio()
    negocio = info.get("negocio", {})
    return {
        "telefono_oficina": negocio.get("telefono_oficina", []),
        "telefono_emergencias": negocio.get("telefono_emergencias", "11-6502-4510"),
        "email": negocio.get("email", "Ascensorescarballino@gmail.com"),
        "horario_oficina": negocio.get("horario_oficina", "Lunes a Viernes 8:00 a 18:00hs"),
        "horario_emergencias": negocio.get("horario_emergencias", "Lunes a Viernes después de las 18hs, Sábados, Domingos y Feriados las 24hs"),
    }


def registrar_solicitud_servicio(
    consorcio: str,
    direccion: str,
    contacto: str,
    telefono_contacto: str,
    descripcion: str,
    urgencia: str = "rutinario"
) -> dict:
    """
    Registra una solicitud de servicio en un archivo de log.
    En producción, esto se conectaría a un CRM o sistema de tickets.

    Args:
        consorcio: Nombre del consorcio, hotel o empresa
        direccion: Dirección completa del edificio
        contacto: Nombre y cargo de la persona de contacto
        telefono_contacto: Teléfono de la persona de contacto
        descripcion: Descripción del servicio requerido
        urgencia: "rutinario", "urgente" o "emergencia"

    Returns:
        Diccionario con el número de solicitud y confirmación
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    numero_solicitud = f"SOL-{timestamp}"

    solicitud = {
        "numero": numero_solicitud,
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "consorcio": consorcio,
        "direccion": direccion,
        "contacto": contacto,
        "telefono_contacto": telefono_contacto,
        "descripcion": descripcion,
        "urgencia": urgencia,
        "estado": "pendiente",
    }

    # Guardar en archivo de log de solicitudes
    os.makedirs("data", exist_ok=True)
    with open("data/solicitudes.log", "a", encoding="utf-8") as f:
        f.write(f"{solicitud}\n")

    logger.info(f"Nueva solicitud registrada: {numero_solicitud} — {consorcio} — {urgencia}")
    return solicitud


async def notificar_grupo_solicitud(telefono_cliente: str, resumen: str, proveedor=None, solicitud_id: int = 0) -> bool:
    """
    Envía un resumen de la solicitud al grupo interno de WhatsApp.
    Se llama automáticamente cuando Olivia completa la recopilación de datos.
    Incluye el ID (#N) para que los técnicos puedan referenciarlo al responder.
    """
    group_id = os.getenv("WHAPI_GROUP_ID", "")
    if not group_id:
        logger.warning("WHAPI_GROUP_ID no configurado — notificación no enviada")
        return False

    id_str = f" #{solicitud_id}" if solicitud_id else ""
    mensaje = (
        f"📋 *NUEVA SOLICITUD DE SERVICIO{id_str}*\n"
        f"─────────────────────────\n"
        f"{resumen}\n"
        f"─────────────────────────\n"
        f"📱 WhatsApp cliente: {telefono_cliente}\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}hs\n"
        f"─────────────────────────\n"
        f"✏️ Responder: *LISTO #{solicitud_id}* o *PENDIENTE #{solicitud_id} [motivo]*"
    )

    if proveedor:
        # Z-API usa formato "{id}-group" para grupos
        group_id_zapi = group_id.replace("@g.us", "-group")
        logger.info(f"Enviando al grupo: '{group_id_zapi}'")
        resultado = await proveedor.enviar_mensaje(group_id_zapi, mensaje)
        if resultado:
            logger.info("Solicitud notificada al grupo interno")
        return resultado

    logger.warning("No hay proveedor disponible para notificar al grupo")
    return False


def es_emergencia(texto: str) -> bool:
    """
    Detecta si el mensaje del cliente describe una emergencia.
    Útil para priorizar la respuesta y dar el número de emergencias.
    """
    palabras_emergencia = [
        "atrapado", "encerrado", "trabado", "parado", "caída", "caido",
        "urgente", "urgencia", "emergencia", "no funciona", "roto",
        "bloqueado", "persona adentro", "ayuda", "socorro",
        "no abre", "no cierra", "detenido", "falla",
    ]
    texto_lower = texto.lower()
    return any(palabra in texto_lower for palabra in palabras_emergencia)


def obtener_info_servicios() -> str:
    """Retorna descripción de los servicios disponibles."""
    return """
Servicios de Ascensores Carballino:

1. MANTENIMIENTO DE ASCENSORES
   - Mantenimiento preventivo periódico
   - Revisión de sistemas eléctricos y mecánicos
   - Lubricación y ajuste de componentes
   - Certificaciones y habilitaciones

2. INSTALACIÓN DE ASCENSORES
   - Instalación de ascensores nuevos
   - Asesoramiento técnico previo a la instalación

3. MODERNIZACIÓN
   - Actualización de ascensores antiguos
   - Mejora de sistemas de control y seguridad
   - Renovación de cabinas

4. MANTENIMIENTO DE BOMBAS ELEVADORAS DE AGUA
   - Mantenimiento preventivo y correctivo
   - Reparación de bombas

5. SERVICIO DE EMERGENCIAS 24HS
   - Atención técnica inmediata
   - Disponible los 365 días del año
   - Contacto: 11-6502-4510 (solo llamadas telefónicas)
"""
