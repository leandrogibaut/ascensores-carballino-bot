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
    buscar_solicitud_por_id, tiene_mensajes_recientes,
    obtener_solicitud_activa_por_telefono,
    buscar_solicitud_por_mensaje_grupo,
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
ADMIN_PHONE = "5491131815195"  # Número del administrador

# Estado del bot (activo por defecto)
bot_activo = True

# ── Menú inicial ──
MENSAJE_MENU = (
    "👋 ¡Bienvenido/a a *Ascensores Carballino*!\n\n"
    "¿En qué podemos ayudarle? Responda con el número de la opción:\n\n"
    "1️⃣ Reclamo / Servicio Técnico\n"
    "2️⃣ Administración / Pagos"
)
# BOTONES_MENU desactivado — Z-API no entrega botones de forma confiable
# BOTONES_MENU = [
#     {"id": "RECLAMO", "label": "🔧 Reclamo / Servicio Técnico"},
#     {"id": "ADM",     "label": "💼 Administración / Pagos"},
# ]
MENSAJE_ADM = (
    "En breve se comunicarán con usted de administración. "
    "Tenga en cuenta que los horarios de administración son "
    "de 8 a 18hs de lunes a viernes."
)
# Estado del menú por teléfono: None = no visto | "esperando_menu" | "reclamo" | "administracion"
sesion_menu: dict[str, str] = {}

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
    Analiza el mensaje del técnico. Se llama DESPUÉS de identificar la solicitud.
    Retorna (estado, notas).
    """
    texto_lower = texto.lower().strip()

    PALABRAS_LISTO = ["listo", "ok", "hecho", "terminado", "resuelto", "solucionado", "andando", "funcionando"]
    PALABRAS_PENDIENTE = ["falta", "hay que", "queda pendiente", "no pude", "no puedo", "mañana", "pendiente", "espera", "esperando"]

    tiene_listo = any(p in texto_lower for p in PALABRAS_LISTO)
    tiene_pendiente = any(p in texto_lower for p in PALABRAS_PENDIENTE)

    if tiene_listo and tiene_pendiente:
        return "pendiente_con_nota", texto
    if tiene_listo:
        return "resuelto", texto
    if tiene_pendiente:
        return "pendiente_con_nota", texto
    # Sin palabras claras: lo tratamos como nota informativa sobre la solicitud
    return "pendiente_con_nota", texto


async def enviar_resumen_diario():
    """Genera y envía el resumen del día al grupo interno a las 20:00hs."""
    solicitudes = await obtener_solicitudes_del_dia()
    hoy = datetime.now().strftime("%d/%m/%Y")

    grupo_zapi = GRUPO_INTERNO.replace("@g.us", "-group")
    if not solicitudes:
        msg = f"📊 *RESUMEN DEL DÍA — {hoy}*\n\nSin solicitudes registradas hoy."
        await proveedor.enviar_mensaje(grupo_zapi, msg)
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

    await proveedor.enviar_mensaje(grupo_zapi, "\n".join(lineas))
    logger.info(f"Resumen diario enviado al grupo: {len(solicitudes)} solicitudes")


# ── Debounce: acumular mensajes por teléfono antes de procesar ──
DEBOUNCE_SEGUNDOS = 10
mensajes_pendientes: dict[str, list[str]] = {}
tareas_pendientes: dict[str, "asyncio.Task"] = {}

scheduler = None


async def iniciar_servicios():
    """Inicializa DB y scheduler en background para no demorar el startup."""
    global scheduler
    await inicializar_db()
    logger.info("Base de datos inicializada")
    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
    scheduler.add_job(enviar_resumen_diario, CronTrigger(hour=20, minute=0, timezone="America/Argentina/Buenos_Aires"))
    scheduler.start()
    logger.info("Scheduler iniciado — resumen diario a las 20:00hs")


async def procesar_mensaje_cliente(telefono: str, texto: str):
    """Procesa un mensaje de cliente y genera respuesta de Olivia."""
    import asyncio
    historial = await obtener_historial(telefono)
    respuesta = await generar_respuesta(texto, historial)

    tag_match = re.search(r'\[SOLICITUD_COMPLETA:(.+?)\]', respuesta, re.DOTALL)
    if tag_match:
        # Verificar si ya existe una solicitud registrada hoy para este número
        solicitud_existente = await obtener_solicitud_activa_por_telefono(telefono)
        if solicitud_existente:
            logger.info(f"Solicitud #{solicitud_existente.id} ya registrada para {telefono} — tag duplicado ignorado")
        else:
            datos_raw = tag_match.group(1).strip()
            resumen_texto, extraido = formatear_resumen_solicitud(datos_raw)
            solicitud_id = await guardar_solicitud({
                "telefono_cliente": telefono,
                "tipo": extraido.get("tipo", ""),
                "nombre": extraido.get("nombre", ""),
                "consorcio": extraido.get("consorcio", ""),
                "direccion": extraido.get("direccion", ""),
                "quien_abre": extraido.get("quien_abre", ""),
                "piso_depto": extraido.get("piso_depto", ""),
            })
            await notificar_grupo_solicitud(telefono, resumen_texto, proveedor, solicitud_id)
        respuesta = re.sub(r'\[SOLICITUD_COMPLETA:.+?\]', '', respuesta, flags=re.DOTALL).strip()

    await guardar_mensaje(telefono, "user", texto)
    await guardar_mensaje(telefono, "assistant", respuesta)
    await proveedor.enviar_mensaje(telefono, respuesta)
    logger.info(f"Respuesta a {telefono}: {respuesta}")


async def procesar_acumulados(telefono: str):
    """Espera el debounce y procesa todos los mensajes acumulados juntos."""
    import asyncio
    await asyncio.sleep(DEBOUNCE_SEGUNDOS)

    textos = mensajes_pendientes.pop(telefono, [])
    tareas_pendientes.pop(telefono, None)

    if not textos:
        return

    texto_combinado = "\n".join(textos)
    logger.info(f"Procesando {len(textos)} mensaje(s) acumulados de {telefono}: {texto_combinado}")

    # ── Lógica del menú inicial ──
    estado = sesion_menu.get(telefono)

    if estado is None or (estado == "reclamo" and not await tiene_mensajes_recientes(telefono)):
        # Sin estado previo, o la sesión expiró (más de 4hs sin actividad) → menú nuevo
        if await tiene_mensajes_recientes(telefono):
            sesion_menu[telefono] = "reclamo"  # Conversación activa, no interrumpir
        else:
            sesion_menu[telefono] = "esperando_menu"
            await proveedor.enviar_mensaje(telefono, MENSAJE_MENU)
            return

    if sesion_menu[telefono] == "esperando_menu":
        # Normalizar respuesta: minúsculas y sin tildes para match flexible
        texto_norm = texto_combinado.strip().lower()
        texto_norm = texto_norm.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
        if texto_norm in ("reclamo", "1", "uno"):
            sesion_menu[telefono] = "reclamo"
            await procesar_mensaje_cliente(telefono, "Hola, quiero hacer un reclamo o solicitar servicio técnico.")
        elif texto_norm in ("adm", "2", "dos", "administracion", "pagos"):
            sesion_menu[telefono] = "administracion"
            await guardar_mensaje(telefono, "user", texto_combinado)
            await guardar_mensaje(telefono, "assistant", MENSAJE_ADM)
            await proveedor.enviar_mensaje(telefono, MENSAJE_ADM)
        else:
            await proveedor.enviar_mensaje(telefono, MENSAJE_MENU)
        return

    if sesion_menu[telefono] == "administracion":
        # Si vuelve a escribir después de elegir administración, repetir el mensaje
        await proveedor.enviar_mensaje(telefono, MENSAJE_ADM)
        return

    # "reclamo" → flujo normal con Olivia
    await procesar_mensaje_cliente(telefono, texto_combinado)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranca servicios en background y cede control inmediatamente."""
    import asyncio
    asyncio.create_task(iniciar_servicios())
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    yield
    if scheduler and scheduler.running:
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

            # ── Comandos del administrador ──
            global bot_activo
            telefono_limpio = msg.telefono.replace("@s.whatsapp.net", "").replace("+", "")
            if telefono_limpio == ADMIN_PHONE or msg.telefono == ADMIN_PHONE:
                comando = msg.texto.strip().upper()
                if comando == "PAUSA BOT":
                    bot_activo = False
                    await proveedor.enviar_mensaje(msg.telefono, "⏸️ Bot pausado. Los mensajes no serán respondidos automáticamente.")
                    continue
                elif comando == "ACTIVAR BOT":
                    bot_activo = True
                    await proveedor.enviar_mensaje(msg.telefono, "▶️ Bot activado. Olivia vuelve a responder automáticamente.")
                    continue

            # ── Si el bot está pausado, ignorar mensajes ──
            if not bot_activo:
                continue

            # ── Mensajes del grupo interno (técnicos reportando) ──
            # Normalizar formato: Z-API usa "123-group", Whapi usa "123@g.us"
            telefono_norm = msg.telefono.replace("-group", "").replace("@g.us", "")
            grupo_norm = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
            if telefono_norm == grupo_norm and grupo_norm:
                solicitud = None

                # Prioridad 1: si es un reply, buscar por el messageId al que responde
                if msg.reference_message_id:
                    solicitud = await buscar_solicitud_por_mensaje_grupo(msg.reference_message_id)
                    if solicitud:
                        logger.info(f"Solicitud #{solicitud.id} identificada por reply (referenceMessageId={msg.reference_message_id})")

                # Prioridad 2: matching por #N
                if not solicitud:
                    id_match = re.search(r'#(\d+)', msg.texto)
                    if id_match:
                        solicitud = await buscar_solicitud_por_id(int(id_match.group(1)))
                        if solicitud:
                            logger.info(f"Solicitud #{solicitud.id} identificada por #N")

                # Prioridad 3: matching por dirección o consorcio
                if not solicitud:
                    solicitud = await buscar_solicitud_por_direccion(msg.texto)
                    if solicitud:
                        logger.info(f"Solicitud #{solicitud.id} identificada por dirección")

                if solicitud:
                    estado, notas = analizar_mensaje_tecnico(msg.texto)
                    await actualizar_estado_solicitud(solicitud.id, estado, notas)
                    logger.info(f"Solicitud #{solicitud.id} actualizada a '{estado}': {msg.texto}")
                else:
                    logger.warning(f"Mensaje técnico sin solicitud coincidente: {msg.texto}")
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

            # ── Debounce: acumular mensajes y esperar 10 segundos de silencio ──
            import asyncio
            if msg.telefono not in mensajes_pendientes:
                mensajes_pendientes[msg.telefono] = []
            mensajes_pendientes[msg.telefono].append(msg.texto)

            # Cancelar tarea anterior si existe
            tarea_anterior = tareas_pendientes.get(msg.telefono)
            if tarea_anterior and not tarea_anterior.done():
                tarea_anterior.cancel()

            # Programar nueva tarea con el timer reseteado
            tareas_pendientes[msg.telefono] = asyncio.create_task(
                procesar_acumulados(msg.telefono)
            )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
