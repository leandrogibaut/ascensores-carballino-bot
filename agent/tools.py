# agent/tools.py — Herramientas del agente
# Generado por AgentKit

"""
Herramientas específicas de Ascensores Carballino.
Permiten al agente gestionar solicitudes de servicio y mantenimiento.
"""

import yaml
import logging

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


async def notificar_grupo_solicitud(telefono_cliente: str, resumen: str, proveedor=None, solicitud_id: int = 0) -> str | bool | None:
    """
    Envía un resumen de la solicitud al grupo interno de WhatsApp usando el
    canal dedicado del proveedor (enviar_mensaje_grupo), el único autorizado
    a alcanzar un destino grupal. Incluye el ID (#N) para que los técnicos
    puedan referenciarlo al responder.
    """
    if not proveedor:
        logger.warning("No hay proveedor disponible para notificar al grupo")
        return None

    mensaje = f"#{solicitud_id} — {resumen}" if solicitud_id else resumen
    logger.info(f"ENVIANDO RECLAMO A GRUPO | solicitud #{solicitud_id} | cliente {telefono_cliente}")
    resultado = await proveedor.enviar_mensaje_grupo(mensaje)
    if resultado:
        logger.info(f"RESPUESTA GRUPO | messageId: {resultado}")
        if solicitud_id:
            from agent.memory import actualizar_mensaje_grupo_id
            await actualizar_mensaje_grupo_id(solicitud_id, resultado)
            logger.info(f"Solicitud #{solicitud_id} vinculada al mensaje del grupo {resultado}")
    else:
        logger.error(f"ERROR ENVÍO GRUPO | solicitud #{solicitud_id}")
    return resultado


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
