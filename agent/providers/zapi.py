# agent/providers/zapi.py — Adaptador para Z-API
# Generado por AgentKit

import os
import json
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
        logger.info(f"Z-API webhook body:\n{json.dumps(body, indent=2, ensure_ascii=False)}")

        # Auto-detectar instance_id del payload si no está en env
        if not self._instance_id and body.get("instanceId"):
            self._instance_id = body["instanceId"]
            logger.info(f"Z-API instance_id auto-detectado: {self._instance_id}")

        # 2B: descartar mensajes borrados (REVOKE) antes de procesar nada más
        if body.get("notification") == "REVOKE":
            logger.debug("Webhook REVOKE ignorado")
            return []

        # ── PRIORIDAD CRÍTICA: detectar intervención humana ANTES del filtro de tipo ──
        # fromMe=true + fromApi=false = humano escribió manualmente desde el teléfono.
        # Z-API puede enviar esto con type=SentCallback u otro tipo que el filtro descartaría.
        if body.get("fromMe") and not body.get("fromApi") and body.get("phone"):
            telefono_humano = body.get("phone", "")
            logger.info(
                f"Z-API fromMe=true fromApi=false — intervención humana en {telefono_humano} "
                f"| type: {body.get('type', '?')} | messageId: {body.get('messageId', '')}"
            )
            return [MensajeEntrante(
                telefono=telefono_humano,
                texto="",
                mensaje_id=body.get("messageId", ""),
                es_propio=True,
                message_id=body.get("messageId"),
                nombre_remitente=body.get("senderName", ""),
                from_api=False,
            )]

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
        elif "buttonsResponseMessage" in body:
            # Respuesta a botón interactivo — usar el ID del botón
            texto = body["buttonsResponseMessage"].get("buttonId", "")
        elif "listResponseMessage" in body:
            # Respuesta a lista interactiva — usar el ID de la fila
            texto = body["listResponseMessage"].get("rowId", "")
        elif "text" in body:
            texto = body["text"].get("message", "")
        elif "image" in body:
            # Foto con caption — los técnicos suelen mandar fotos con descripción
            texto = body["image"].get("caption", "")
        elif "video" in body:
            texto = body["video"].get("caption", "")
        elif "document" in body:
            texto = body["document"].get("caption", "")
        elif subtipo in ("audio", "ptt"):
            audio_url = body.get("audio", {}).get("audioUrl", "")
            if audio_url:
                texto = await self._transcribir_audio_url(audio_url)
                if texto:
                    logger.info(f"Audio transcripto de {telefono}: {texto}")
                else:
                    logger.warning(f"Transcripción de audio fallida o vacía para {telefono} — mensaje descartado")
            # Sin transcripción: texto queda vacío → se descarta en el check de abajo
        elif notificacion == "CALL_VOICE":
            # Llamada en curso — ignorar, ya respondemos en CALL_MISSED_VOICE
            return []
        else:
            pass  # tipo desconocido — continuar para que fromMe=True llegue a main.py

        # Mensajes de cliente sin texto reconocido: descartar
        # Mensajes propios (fromMe=True) sin texto: pasar igual para registrar intervención humana
        if not texto and not es_propio:
            return []

        quoted = body.get("quotedMessage", {}) or {}
        texto_citado = quoted.get("text", {}).get("message") or quoted.get("caption")

        mensajes.append(MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=body.get("messageId", ""),
            es_propio=es_propio,
            reference_message_id=body.get("referenceMessageId"),
            message_id=body.get("messageId"),
            nombre_remitente=body.get("senderName", ""),
            texto_citado=texto_citado or None,
            from_api=body.get("fromApi", False),
        ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> str | None:
        """Envía mensaje de texto via Z-API. Retorna el messageId si fue exitoso, None si falló."""
        logger.info(f"Z-API enviar — instance_id: {bool(self._instance_id)}, token: {bool(self._token)}")
        if not self._instance_id or not self._token:
            logger.warning("ZAPI_INSTANCE_ID o ZAPI_TOKEN no disponibles")
            return None
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"{self._get_base_url()}/send-text"
            logger.info(f"Z-API POST {url}")
            r = await client.post(
                url,
                json={"phone": telefono, "message": mensaje},
                headers=self._headers(),
            )
            logger.info(f"Z-API respuesta envío: {r.status_code} — {json.dumps(r.json(), indent=2, ensure_ascii=False)}")
            if r.status_code not in (200, 201):
                logger.error(f"Error Z-API envío: {r.status_code} — {r.text}")
                return None
            data = r.json()
            return data.get("messageId") or data.get("id")

    async def enviar_menu_botones(self, telefono: str, texto: str, botones: list[dict]) -> bool:
        """Envía un mensaje con botones interactivos via Z-API (send-button-list)."""
        if not self._instance_id or not self._token:
            logger.warning("ZAPI_INSTANCE_ID o ZAPI_TOKEN no disponibles")
            return False
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"{self._get_base_url()}/send-button-list"
            payload = {
                "phone": telefono,
                "message": texto,
                "buttonList": {
                    "buttons": [
                        {"id": b["id"], "label": b["label"]}
                        for b in botones
                    ]
                }
            }
            r = await client.post(url, json=payload, headers=self._headers())
            if r.status_code not in (200, 201):
                logger.warning(f"Z-API botones falló ({r.status_code}), enviando texto plano")
                # Fallback a texto plano si los botones no funcionan
                opciones = "\n".join(f"*{b['id']}* — {b['label']}" for b in botones)
                return await self.enviar_mensaje(telefono, f"{texto}\n\n{opciones}")
            return True

    async def _transcribir_audio_url(self, audio_url: str) -> str:
        """Descarga el audio desde la URL de Z-API y lo transcribe con Groq."""
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            logger.warning("GROQ_API_KEY no configurada — transcripción de audio deshabilitada")
            return ""
        async with httpx.AsyncClient(timeout=30) as client:
            logger.info(f"Descargando audio desde Z-API: {audio_url}")
            r = await client.get(audio_url)
            logger.info(f"Descarga audio — status: {r.status_code}, content-type: {r.headers.get('content-type', '(sin content-type)')}")
            if r.status_code != 200:
                logger.error(f"Error descargando audio Z-API: {r.status_code} — {r.text[:200]}")
                return ""
            audio_bytes = r.content
            logger.info(f"Audio descargado — tamaño: {len(audio_bytes)} bytes")
            content_type = r.headers.get("content-type", "audio/ogg")
            extension = "ogg"
            if "mp4" in content_type or "mpeg" in content_type:
                extension = "mp3"
            nombre_archivo = f"audio.{extension}"
            logger.info(f"Enviando a Groq Whisper — archivo: {nombre_archivo}, content-type: {content_type}")
            groq_headers = {"Authorization": f"Bearer {groq_key}"}
            r2 = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=groq_headers,
                files={"file": (nombre_archivo, audio_bytes, content_type)},
                data={"model": "whisper-large-v3-turbo", "language": "es"},
            )
            logger.info(f"Groq Whisper — status: {r2.status_code}")
            if r2.status_code != 200:
                logger.error(f"Error Groq Whisper: {r2.status_code} — {r2.text[:200]}")
                return ""
            texto = r2.json().get("text", "").strip()
            logger.info(f"Transcripción exitosa — {len(texto)} caracteres")
            return texto
