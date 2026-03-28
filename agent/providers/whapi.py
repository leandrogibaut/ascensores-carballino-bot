# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


async def transcribir_audio(media_id: str, whapi_token: str) -> str:
    """
    Descarga el audio desde Whapi y lo transcribe con Groq Whisper.
    Retorna el texto transcripto o cadena vacía si falla.
    """
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        logger.warning("GROQ_API_KEY no configurada — audio no transcripto")
        return ""

    headers_whapi = {"Authorization": f"Bearer {whapi_token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Descargar el audio desde Whapi
        r = await client.get(
            f"https://gate.whapi.cloud/media/{media_id}",
            headers=headers_whapi
        )
        if r.status_code != 200:
            logger.error(f"Error descargando audio de Whapi: {r.status_code}")
            return ""

        audio_bytes = r.content
        content_type = r.headers.get("content-type", "audio/ogg")

        # Determinar extensión según el content-type
        extension = "ogg"
        if "mp4" in content_type or "mpeg" in content_type:
            extension = "mp3"
        elif "wav" in content_type:
            extension = "wav"
        elif "webm" in content_type:
            extension = "webm"

        # Enviar a Groq Whisper para transcripción
        groq_headers = {"Authorization": f"Bearer {groq_key}"}
        r2 = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers=groq_headers,
            files={"file": (f"audio.{extension}", audio_bytes, content_type)},
            data={"model": "whisper-large-v3-turbo", "language": "es"},
        )
        if r2.status_code != 200:
            logger.error(f"Error en Groq Whisper: {r2.status_code} — {r2.text}")
            return ""

        texto = r2.json().get("text", "").strip()
        logger.info(f"Audio transcripto: {texto}")
        return texto


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud. Ignora grupos. Transcribe audios."""
        body = await request.json()
        mensajes = []
        for msg in body.get("messages", []):
            chat_id = msg.get("chat_id", "")
            tipo = msg.get("type", "text")

            # Ignorar mensajes de grupos EXCEPTO el grupo interno
            grupo_interno = os.getenv("WHAPI_GROUP_ID", "")
            if chat_id.endswith("@g.us") and chat_id != grupo_interno:
                logger.debug(f"Mensaje de grupo ignorado: {chat_id}")
                continue

            texto = ""

            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")

            elif tipo in ("audio", "ptt"):
                # ptt = push-to-talk (nota de voz de WhatsApp)
                media_id = msg.get(tipo, {}).get("id", "")
                if media_id and self.token:
                    texto = await transcribir_audio(media_id, self.token)
                    if not texto:
                        texto = "[Audio no transcripto]"

            elif tipo == "call":
                # Intento de llamada por WhatsApp — responder con mensaje automático
                texto = "__llamada_whatsapp__"

            else:
                # Ignorar otros tipos (imagen, video, documento, etc.)
                logger.debug(f"Tipo de mensaje no soportado: {tipo}")
                continue

            mensajes.append(MensajeEntrante(
                telefono=chat_id,
                texto=texto,
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
            ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.url_envio,
                json={"to": telefono, "body": mensaje},
                headers=headers,
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi: {r.status_code} — {r.text}")
            return r.status_code == 200
