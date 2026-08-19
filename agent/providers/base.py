# agent/providers/base.py — Clase base para proveedores de WhatsApp
# Generado por AgentKit

"""
Define la interfaz común que todos los proveedores de WhatsApp deben implementar.
Esto permite cambiar de proveedor sin modificar el resto del código.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request

logger = logging.getLogger("agentkit")


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str       # Número del remitente
    texto: str          # Contenido del mensaje
    mensaje_id: str     # ID único del mensaje
    es_propio: bool     # True si lo envió el agente (se ignora)
    reference_message_id: str | None = None  # ID del mensaje citado (reply)
    message_id: str | None = None            # ID propio del mensaje (para tracking)
    nombre_remitente: str = ""               # Nombre del remitente (útil en grupos)
    texto_citado: str | None = None          # Texto del mensaje citado (si Z-API lo trae)
    from_api: bool = False                   # True si fue enviado por la API (no por un humano)
    es_audio: bool = False                   # True si texto proviene de una transcripción interna


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> str | None:
        """Envía un mensaje de texto. Retorna el messageId si fue exitoso, None si falló."""
        ...

    async def enviar_menu_botones(self, telefono: str, texto: str, botones: list[dict]) -> bool:
        """
        Envía un mensaje con botones interactivos.
        botones: lista de {"id": "...", "label": "..."}
        Fallback: envía texto plano si el proveedor no soporta botones.
        """
        opciones = "\n".join(f"*{b['id']}* — {b['label']}" for b in botones)
        return await self.enviar_mensaje(telefono, f"{texto}\n\n{opciones}")

    async def enviar_mensaje_grupo(self, mensaje: str) -> str | bool | None:
        """
        Envía un mensaje al grupo interno configurado (WHAPI_GROUP_ID).
        Es el único método habilitado para alcanzar un grupo: no recibe un
        destino arbitrario, por lo que enviar_mensaje() sigue bloqueando
        cualquier destino grupal para todo el resto del código.
        """
        logger.warning("enviar_mensaje_grupo() no implementado para este proveedor")
        return None

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None
