# agent/providers/zapi.py — Adaptador para Z-API
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorZapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Z-API."""

    def _get_base_url(self) -> str:
        instance_id = os.getenv("ZAPI_INSTANCE_ID")
        token = os.getenv("ZAPI_TOKEN")
        return f"https://api.z-api.io/instances/{instance_id}/token/{token}"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        client_token = os.getenv("ZAPI_CLIENT_TOKEN", "")
        if client_token:
            headers["Client-Token"] = client_token
        return headers

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Z-API. Ignora grupos y mensajes propios."""
        body = await request.json()
        mensajes = []

        # Z-API envía un objeto por webhook, no una lista
        tipo = body.get("type", "")

        # Solo procesar mensajes entrantes de texto y audio
        if tipo not in ("ReceivedCallback",):
            return []

        telefono = body.get("phone", "")
        es_grupo = body.get("isGroup", False)
        es_propio = body.get("fromMe", False)

        # Ignorar grupos (excepto grupo interno)
        grupo_interno = os.getenv("WHAPI_GROUP_ID", "")
        if es_grupo and telefono != grupo_interno:
            logger.debug(f"Mensaje de grupo ignorado: {telefono}")
            return []

        texto = ""
        subtipo = body.get("subtype", "")

        if "text" in body:
            texto = body["text"].get("message", "")
        elif subtipo in ("audio", "ptt"):
            # Audio: Z-API provee una URL de descarga
            audio_url = body.get("audio", {}).get("audioUrl", "")
            if audio_url:
                texto = await self._transcribir_audio_url(audio_url)
            if not texto:
                texto = "[Audio no transcripto]"
        elif subtipo == "call":
            texto = "__llamada_whatsapp__"
        else:
            return []

        mensajes.append(MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=body.get("messageId", ""),
            es_propio=es_propio,
        ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje de texto via Z-API."""
        instance_id = os.getenv("ZAPI_INSTANCE_ID")
        token = os.getenv("ZAPI_TOKEN")
        if not instance_id or not token:
            logger.warning("ZAPI_INSTANCE_ID o ZAPI_TOKEN no configurados")
            return False
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self._get_base_url()}/send-text",
                json={"phone": telefono, "message": mensaje},
                headers=self._headers(),
            )
            if r.status_code not in (200, 201):
                logger.error(f"Error Z-API envío: {r.status_code} — {r.text}")
            return r.status_code in (200, 201)

    async def _transcribir_audio_url(self, audio_url: str) -> str:
        """Descarga el audio desde la URL de Z-API y lo transcribe con Groq."""
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            return ""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(audio_url)
            if r.status_code != 200:
                logger.error(f"Error descargando audio Z-API: {r.status_code}")
                return ""
            audio_bytes = r.content
            content_type = r.headers.get("content-type", "audio/ogg")
            extension = "ogg"
            if "mp4" in content_type or "mpeg" in content_type:
                extension = "mp3"
            groq_headers = {"Authorization": f"Bearer {groq_key}"}
            r2 = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=groq_headers,
                files={"file": (f"audio.{extension}", audio_bytes, content_type)},
                data={"model": "whisper-large-v3-turbo", "language": "es"},
            )
            if r2.status_code != 200:
                logger.error(f"Error Groq Whisper: {r2.status_code}")
                return ""
            return r2.json().get("text", "").strip()
