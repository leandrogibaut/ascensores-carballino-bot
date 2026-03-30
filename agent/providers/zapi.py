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

    def __init__(self):
        # Auto-detectado del primer webhook recibido si no está en env
        self._instance_id = os.getenv("ZAPI_INSTANCE_ID", "")
        self._token = os.getenv("ZAPI_TOKEN", "")

    def _get_base_url(self) -> str:
        return f"https://api.z-api.io/instances/{self._instance_id}/token/{self._token}"

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

        # Log del body completo para diagnóstico
        logger.info(f"Z-API webhook body: {body}")

        # Auto-detectar instance_id del payload si no está en env
        if not self._instance_id and body.get("instanceId"):
            self._instance_id = body["instanceId"]
            logger.info(f"Z-API instance_id auto-detectado: {self._instance_id}")

        # Z-API envía un objeto por webhook, no una lista
        tipo = body.get("type", "")

        # Solo procesar mensajes entrantes de texto y audio
        if tipo not in ("ReceivedCallback",):
            return []

        telefono = body.get("phone", "")
        es_grupo = body.get("isGroup", False)
        es_propio = body.get("fromMe", False)

        # Ignorar grupos (excepto grupo interno)
        # Normalizar formato: Z-API usa "123-group", Whapi usa "123@g.us"
        grupo_interno_raw = os.getenv("WHAPI_GROUP_ID", "")
        grupo_interno_id = grupo_interno_raw.replace("@g.us", "").replace("-group", "")
        telefono_id = telefono.replace("-group", "").replace("@g.us", "")
        if es_grupo and telefono_id != grupo_interno_id:
            logger.debug(f"Mensaje de grupo ignorado: {telefono}")
            return []

        texto = ""
        subtipo = body.get("subtype", "")
        notificacion = body.get("notification", "")

        if notificacion == "CALL_MISSED_VOICE":
            # Llamada perdida — responder automáticamente
            texto = "__llamada_whatsapp__"
        elif "text" in body:
            texto = body["text"].get("message", "")
        elif subtipo in ("audio", "ptt"):
            # Audio: Z-API provee una URL de descarga
            audio_url = body.get("audio", {}).get("audioUrl", "")
            if audio_url:
                texto = await self._transcribir_audio_url(audio_url)
            if not texto:
                texto = "[Audio no transcripto]"
        elif notificacion == "CALL_VOICE":
            # Llamada en curso — ignorar, ya respondemos en CALL_MISSED_VOICE
            return []
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
        logger.info(f"Z-API enviar — instance_id: {bool(self._instance_id)}, token: {bool(self._token)}")
        if not self._instance_id or not self._token:
            logger.warning("ZAPI_INSTANCE_ID o ZAPI_TOKEN no disponibles")
            return False
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"{self._get_base_url()}/send-text"
            logger.info(f"Z-API POST {url}")
            r = await client.post(
                url,
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
