# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

"""
Servidor principal del agente de WhatsApp.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
"""

import os
import re
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    guardar_solicitud, obtener_solicitudes_del_dia,
    actualizar_estado_solicitud, buscar_solicitud_por_direccion,
)
from agent.providers import obtener_proveedor
from agent.tools import notificar_grupo_solicitud

load_dotenv()

# Configuración de logging según entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

# Proveedor de WhatsApp (se configura en .env con WHATSAPP_PROVIDER)
proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
GRUPO_INTERNO = os.getenv("WHAPI_GROUP_ID", "")

# Palabras que indican que algo quedó pendiente
PALABRAS_PENDIENTE = ["pero", "falta", "hay que", "queda", "pendiente", "revisar", "cambiar", "arreglar", "espera"]


def formatear_resumen_solicitud(datos_raw: str) -> str:
    """Convierte el tag interno de solicitud en un mensaje legible para el grupo."""
    campos = {
        "tipo": "📋 Tipo",
        "nombre": "👤 Nombre",
        "tel": "📞 Teléfono",
        "consorcio": "🏢 Consorcio/Empresa",
        "direccion": "📍 Dirección",
        "quien_abre": "🔑 Quién abre",
        "piso_depto": "🏠 Piso/Depto",
    }
    lineas = []
    extraido = {}
    for clave, etiqueta in campos.items():
        match = re.search(rf'{clave}="([^"]*)"', datos_raw)
        if match and match.group(1):
            lineas.append(f"{etiqueta}: {match.group(1)}")
            extraido[clave] = match.group(1)
    return "\n".join(lineas) if lineas else datos_raw, extraido


def analizar_mensaje_tecnico(texto: str) -> tuple[str, str]:
    """
    Analiza el mensaje de un técnico y determina el estado de la solicitud.
    Retorna (estado, notas): estado es 'resuelto' o 'pendiente_con_nota'.
    """
    texto_lower = texto.lower()
    tiene_listo = any(p in texto_lower for p in ["listo", "ok", "hecho", "terminado", "resuelto"])
    tiene_pendiente = any(p in texto_lower for p in PALABRAS_PENDIENTE)

    if tiene_listo and tiene_pendiente:
        return "pendiente_con_nota", texto
    elif tiene_listo:
        return "resuelto", texto
    elif tiene_pendiente:
        return "pendiente_con_nota", texto
    return None, None


async def enviar_resumen_diario():
    """Genera y envía el resumen del día al grupo interno a las 20:00hs."""
    solicitudes = await obtener_solicitudes_del_dia()
    hoy = datetime.now().strftime("%d/%m/%Y")

    if not solicitudes:
        msg = f"📊 *RESUMEN DEL DÍA — {hoy}*\n\nSin solicitudes registradas hoy."
        await proveedor.enviar_mensaje(GRUPO_INTERNO, msg)
        return

    resueltos = [s for s in solicitudes if s.estado == "resuelto"]
    pendientes_nota = [s for s in solicitudes if s.estado == "pendiente_con_nota"]
    pendientes = [s for s in solicitudes if s.estado == "pendiente"]

    lineas = [f"📊 *RESUMEN DEL DÍA — {hoy}*", f"Total: {len(solicitudes)} solicitud(es)\n"]

    if resueltos:
        lineas.append(f"✅ *RESUELTOS ({len(resueltos)})*")
        for s in resueltos:
            lineas.append(f"  • {s.consorcio or s.nombre} — {s.direccion}")
        lineas.append("")

    if pendientes_nota:
        lineas.append(f"⚠️ *PENDIENTES CON NOTA ({len(pendientes_nota)})*")
        for s in pendientes_nota:
            lineas.append(f"  • {s.consorcio or s.nombre} — {s.direccion}")
            if s.notas_tecnico:
                lineas.append(f"    → {s.notas_tecnico}")
        lineas.append("")

    if pendientes:
        lineas.append(f"❌ *SIN RESPUESTA ({len(pendientes)})*")
        for s in pendientes:
            lineas.append(f"  • {s.consorcio or s.nombre} — {s.direccion} ({s.tipo})")

    await proveedor.enviar_mensaje(GRUPO_INTERNO, "\n".join(lineas))
    logger.info(f"Resumen diario enviado al grupo: {len(solicitudes)} solicitudes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos y el scheduler al arrancar el servidor."""
    await inicializar_db()
    logger.info("Base de datos inicializada")

    # Scheduler para el resumen diario a las 20:00hs (hora Argentina UTC-3)
    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
    scheduler.add_job(enviar_resumen_diario, CronTrigger(hour=20, minute=0))
    scheduler.start()
    logger.info("Scheduler iniciado — resumen diario a las 20:00hs")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    yield
    scheduler.shutdown()


app = FastAPI(
    title="AgentKit — Ascensores Carballino",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit", "agente": "Olivia"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook/messages")
@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Procesa el mensaje, genera respuesta con Claude y la envía de vuelta.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            # ── Mensajes del grupo interno (técnicos reportando) ──
            if msg.telefono == GRUPO_INTERNO:
                estado, notas = analizar_mensaje_tecnico(msg.texto)
                if estado:
                    solicitud = await buscar_solicitud_por_direccion(msg.texto)
                    if solicitud:
                        await actualizar_estado_solicitud(solicitud.id, estado, notas)
                        logger.info(f"Solicitud #{solicitud.id} actualizada a '{estado}': {msg.texto}")
                continue

            # ── Respuesta automática a intentos de llamada ──
            if msg.texto == "__llamada_whatsapp__":
                aviso = (
                    "Hola, por este número no atendemos llamadas de WhatsApp. "
                    "Para emergencias llamá al 4301-3967 o escribinos aquí y te atendemos enseguida."
                )
                await proveedor.enviar_mensaje(msg.telefono, aviso)
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            # ── Flujo normal: cliente escribe a Olivia ──
            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(msg.texto, historial)

            # Detectar solicitud completa y guardar en DB + notificar grupo
            tag_match = re.search(r'\[SOLICITUD_COMPLETA:(.+?)\]', respuesta, re.DOTALL)
            if tag_match:
                datos_raw = tag_match.group(1).strip()
                resumen_texto, extraido = formatear_resumen_solicitud(datos_raw)
                # Guardar en base de datos
                await guardar_solicitud({
                    "telefono_cliente": msg.telefono,
                    "tipo": extraido.get("tipo", ""),
                    "nombre": extraido.get("nombre", ""),
                    "consorcio": extraido.get("consorcio", ""),
                    "direccion": extraido.get("direccion", ""),
                    "quien_abre": extraido.get("quien_abre", ""),
                    "piso_depto": extraido.get("piso_depto", ""),
                })
                # Notificar al grupo interno
                await notificar_grupo_solicitud(msg.telefono, resumen_texto)
                # Limpiar tag antes de enviar al cliente
                respuesta = re.sub(r'\[SOLICITUD_COMPLETA:.+?\]', '', respuesta, flags=re.DOTALL).strip()

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)
            await proveedor.enviar_mensaje(msg.telefono, respuesta)
            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
