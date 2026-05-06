# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

"""
Servidor principal del agente de WhatsApp.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
"""

import os
import re
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, date
from zoneinfo import ZoneInfo
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
    marcar_intervencion_humana,
    hay_intervencion_reciente,
    guardar_mensaje_grupo, obtener_mensajes_grupo_del_dia,
    obtener_solicitudes_por_fecha, obtener_mensajes_grupo_por_fecha,
)
from agent.reports import generar_reporte_diario_preview  # noqa: F401
from agent.conocimiento import buscar_cliente_por_texto
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
TELEFONOS_EXCLUIDOS = {"5491122636490"}  # No reciben respuestas automáticas
REPORTE_DIARIO_TELEFONO = "5491122636490"  # Destinatario del reporte diario

# Estado del bot (activo por defecto)
bot_activo = True

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")


def oficina_esta_disponible() -> bool:
    """Retorna True si es día hábil entre 8:00 y 18:00 hora Argentina."""
    ahora = datetime.now(TZ_AR)
    es_dia_habil = ahora.weekday() < 5  # 0=lunes … 4=viernes
    es_horario = 8 <= ahora.hour < 18
    return es_dia_habil and es_horario

def formatear_resumen_solicitud(datos_raw: str) -> tuple[str, dict]:
    """Convierte el tag interno de solicitud en un párrafo corto para el grupo.
    Soporta formato nuevo (texto libre) y formato viejo (clave="valor").
    """
    # Detectar formato viejo por presencia de claves conocidas
    es_formato_viejo = bool(re.search(r'(direccion|tipo|quien_abre)="', datos_raw))

    if es_formato_viejo:
        extraido = {}
        for clave in ("tipo", "nombre", "tel", "consorcio", "direccion", "quien_abre", "piso_depto"):
            match = re.search(rf'{clave}="([^"]*)"', datos_raw)
            if match and match.group(1):
                extraido[clave] = match.group(1)

        direccion = extraido.get("direccion", "")
        tipo = extraido.get("tipo", "")
        quien_abre = extraido.get("quien_abre", "")
        piso_depto = extraido.get("piso_depto", "")

        partes_resumen = []
        if direccion and tipo:
            partes_resumen.append(f"{direccion}, {tipo}.")
        elif direccion:
            partes_resumen.append(f"{direccion}.")
        elif tipo:
            partes_resumen.append(f"{tipo}.")
        if quien_abre:
            abre = f"Abre {quien_abre}"
            if piso_depto and piso_depto.upper() != "N/A":
                abre += f" ({piso_depto})"
            partes_resumen.append(abre + ".")

        resumen = " ".join(partes_resumen) if partes_resumen else datos_raw

    else:
        # Formato nuevo: texto libre — limpiar y extraer partes por punto
        resumen = " ".join(datos_raw.split())
        partes = [p.strip() for p in resumen.split(".") if p.strip()]
        extraido = {}
        if len(partes) >= 1:
            extraido["direccion"] = partes[0]
        if len(partes) >= 2:
            extraido["tipo"] = partes[1]
        if len(partes) >= 3:
            extraido["quien_abre"] = ". ".join(partes[2:])

    # Normalización silenciosa: si la dirección coincide con un cliente conocido,
    # reemplazar dirección y consorcio con los valores canónicos del YAML.
    cliente = buscar_cliente_por_texto(datos_raw)
    if cliente:
        old_dir = extraido.get("direccion", "")
        extraido["direccion"] = cliente["direccion"]
        extraido["consorcio"] = cliente["nombre"]
        new_dir = cliente["direccion"]
        if old_dir and old_dir in resumen:
            resumen = resumen.replace(old_dir, new_dir, 1)
        elif not old_dir:
            resumen = new_dir + (". " + resumen if resumen else "")

    return resumen, extraido


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
    """Envía a las 20:00hs un JSON con los mensajes del grupo del día al número configurado."""
    try:
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        hoy = datetime.now(tz).date()

        mensajes = await obtener_mensajes_grupo_por_fecha(hoy)

        payload = {
            "source": "Reclamos Ascensores Carballino",
            "date": hoy.isoformat(),
            "timezone": "America/Argentina/Buenos_Aires",
            "messages": [
                {
                    "time": m.timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%H:%M"),
                    "sender": m.nombre_remitente or "",
                    "phone": m.telefono_remitente or "",
                    "text": m.texto or "",
                    "message_id": m.mensaje_id or "",
                    "reference_message_id": m.reference_message_id or "",
                    "quoted_text": m.texto_citado or "",
                }
                for m in mensajes
            ],
        }

        texto_json = json.dumps(payload, ensure_ascii=False)
        resultado = await proveedor.enviar_mensaje(REPORTE_DIARIO_TELEFONO, texto_json)
        if resultado:
            logger.info(f"Reporte diario enviado a {REPORTE_DIARIO_TELEFONO} ({len(mensajes)} mensajes)")
        else:
            logger.error(f"Error al enviar reporte diario a {REPORTE_DIARIO_TELEFONO}")
    except Exception as e:
        logger.error(f"Error generando reporte diario: {e}")


# ── Debounce: acumular mensajes por teléfono antes de procesar ──
DEBOUNCE_SEGUNDOS = 10          # conversación activa (historial reciente)
DEBOUNCE_NUEVO_SEGUNDOS = 120   # primer mensaje / conversación nueva (sin historial)
mensajes_pendientes: dict[str, list[str]] = {}
tareas_pendientes: dict[str, "asyncio.Task"] = {}
_MAX_IDS_PROCESADOS = 5000
mensajes_webhook_procesados: set[str] = set()
mensajes_enviados_por_bot: set[str] = set()

conversaciones_estado: dict[str, dict] = {}

def marcar_estado_conversacion(telefono: str, estado: str):
    conversaciones_estado[telefono] = {"estado": estado, "timestamp": datetime.now()}

def obtener_estado_conversacion(telefono: str) -> str | None:
    datos = conversaciones_estado.get(telefono)
    if not datos:
        return None
    if (datetime.now() - datos["timestamp"]).total_seconds() > 86400:
        del conversaciones_estado[telefono]
        return None
    return datos["estado"]

def limpiar_estado_conversacion(telefono: str):
    conversaciones_estado.pop(telefono, None)

scheduler = None


async def iniciar_servicios():
    """Inicializa DB y scheduler en background para no demorar el startup."""
    global scheduler
    await inicializar_db()
    logger.info("Base de datos inicializada")
    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
    scheduler.add_job(enviar_resumen_diario, CronTrigger(hour=20, minute=0, timezone="America/Argentina/Buenos_Aires"))
    scheduler.start()
    logger.info("Scheduler iniciado — reporte diario a las 20:00hs")


async def _enviar_registrando(telefono: str, mensaje: str) -> str | None:
    """Envía un mensaje y registra su messageId para detectar intervenciones humanas futuras."""
    msg_id = await proveedor.enviar_mensaje(telefono, mensaje)
    if msg_id:
        mensajes_enviados_por_bot.add(msg_id)
        if len(mensajes_enviados_por_bot) > _MAX_IDS_PROCESADOS:
            mensajes_enviados_por_bot.clear()
    return msg_id


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
        if not respuesta:
            respuesta = "Perfecto, ya registramos el reclamo y lo derivamos al equipo técnico. Muchas gracias."

    if not respuesta or not respuesta.strip():
        logger.error(f"Respuesta vacía generada para {telefono}. No se envía mensaje.")
        return

    await guardar_mensaje(telefono, "user", texto)
    await guardar_mensaje(telefono, "assistant", respuesta)
    await _enviar_registrando(telefono, respuesta)
    logger.info(f"Respuesta a {telefono}: {respuesta}")


async def procesar_acumulados(telefono: str):
    from agent.brain import clasificar_intencion
    debounce = DEBOUNCE_SEGUNDOS if await tiene_mensajes_recientes(telefono) else DEBOUNCE_NUEVO_SEGUNDOS
    await asyncio.sleep(debounce)
    textos = mensajes_pendientes.pop(telefono, [])
    tareas_pendientes.pop(telefono, None)
    if not textos:
        return
    texto_combinado = "\n".join(textos)
    logger.info(f"Procesando {len(textos)} mensaje(s) de {telefono}: {texto_combinado}")
    if await hay_intervencion_reciente(telefono):
        logger.info(f"{telefono} silenciado por intervención humana")
        return
    estado_conv = obtener_estado_conversacion(telefono)
    if estado_conv == "pendiente_admin":
        logger.info(f"{telefono} en pendiente_admin")
        return
    if estado_conv == "esperando_consulta_admin":
        marcar_estado_conversacion(telefono, "pendiente_admin")
        return
    if estado_conv == "reclamo":
        await procesar_mensaje_cliente(telefono, texto_combinado)
        return
    if estado_conv == "esperando_intencion":
        intencion = await clasificar_intencion(texto_combinado)
        if intencion == "reclamo":
            marcar_estado_conversacion(telefono, "reclamo")
            mensaje_inicial = (
                "Hola, soy Olivia de Ascensores Carballino 👋 "
                "Para registrar el reclamo necesito algunos datos:\n\n"
                "• *¿Cuál es la dirección del edificio?*\n"
                "• *¿Qué problema tiene el ascensor?*\n"
                "• *¿Quién abre? (encargado, administrador, etc.) ¿En qué horarios?*\n\n"
                "Puede respondernos con toda la información junta o por partes, no se preocupe 😊"
            )
            await guardar_mensaje(telefono, "assistant", mensaje_inicial)
            await _enviar_registrando(telefono, mensaje_inicial)
            return
        if intencion == "administracion":
            marcar_estado_conversacion(telefono, "esperando_consulta_admin")
            if oficina_esta_disponible():
                mensaje_admin = "Hola 👋 Por favor, ¿cuál es su consulta? En breve la derivamos al área correspondiente."
            else:
                mensaje_admin = (
                    "Gracias por comunicarse con Ascensores Carballino. "
                    "El área de administración atiende de lunes a viernes de 8 a 18hs. "
                    "Por favor deje su consulta y será atendida el próximo día hábil 📋"
                )
            await guardar_mensaje(telefono, "assistant", mensaje_admin)
            await _enviar_registrando(telefono, mensaje_admin)
            return
        # Desconocido: preguntar de forma más específica sin repetir el mismo mensaje
        mensaje_aclarar = (
            "Disculpe, para poder derivarlo correctamente, "
            "¿necesita enviar técnicos por una falla del ascensor?"
        )
        await guardar_mensaje(telefono, "assistant", mensaje_aclarar)
        await _enviar_registrando(telefono, mensaje_aclarar)
        return
    intencion = await clasificar_intencion(texto_combinado)
    if intencion == "reclamo":
        marcar_estado_conversacion(telefono, "reclamo")
        mensaje_inicial = (
            "Hola, soy Olivia de Ascensores Carballino 👋 "
            "Para registrar el reclamo necesito algunos datos:\n\n"
            "• *¿Cuál es la dirección del edificio?*\n"
            "• *¿Qué problema tiene el ascensor?*\n"
            "• *¿Quién abre? (encargado, administrador, etc.) ¿En qué horarios?*\n\n"
            "Puede respondernos con toda la información junta o por partes, no se preocupe 😊"
        )
        await guardar_mensaje(telefono, "assistant", mensaje_inicial)
        await _enviar_registrando(telefono, mensaje_inicial)
        return
    if intencion == "administracion":
        marcar_estado_conversacion(telefono, "esperando_consulta_admin")
        if oficina_esta_disponible():
            mensaje_admin = "Hola 👋 Por favor, ¿cuál es su consulta? En breve la derivamos al área correspondiente."
        else:
            mensaje_admin = (
                "Gracias por comunicarse con Ascensores Carballino. "
                "El área de administración atiende de lunes a viernes de 8 a 18hs. "
                "Por favor deje su consulta y será atendida el próximo día hábil 📋"
            )
        await guardar_mensaje(telefono, "assistant", mensaje_admin)
        await _enviar_registrando(telefono, mensaje_admin)
        return
    marcar_estado_conversacion(telefono, "esperando_intencion")
    mensaje_desconocido = (
        "Hola, soy Olivia de Ascensores Carballino 👋 "
        "¿Me puede indicar si se trata de un *reclamo técnico* "
        "o una *consulta administrativa*?"
    )
    await guardar_mensaje(telefono, "assistant", mensaje_desconocido)
    await _enviar_registrando(telefono, mensaje_desconocido)
    return


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


@app.get("/api/mensajes-grupo-hoy")
async def mensajes_grupo_hoy():
    """Retorna todos los mensajes del grupo interno recibidos hoy."""
    mensajes = await obtener_mensajes_grupo_del_dia()
    return [
        {
            "telefono": m.telefono_remitente,
            "nombre": m.nombre_remitente,
            "texto": m.texto,
            "hora": m.timestamp.strftime("%H:%M"),
        }
        for m in mensajes
    ]


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
            # Mensajes propios (fromMe=True): registrar intervención humana y descartar
            if msg.es_propio:
                telefono_norm_p = msg.telefono.replace("-group", "").replace("@g.us", "")
                grupo_norm_p = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
                if not (telefono_norm_p == grupo_norm_p and grupo_norm_p) and msg.telefono:
                    await marcar_intervencion_humana(msg.telefono)
                    logger.info(f"Intervención humana detectada en conversación con {msg.telefono}, bot silenciado 6hs")
                    tarea_p = tareas_pendientes.pop(msg.telefono, None)
                    if tarea_p and not tarea_p.done():
                        tarea_p.cancel()
                    mensajes_pendientes.pop(msg.telefono, None)
                continue

            if not msg.texto:
                continue

            # Descartar mensajes enviados por la API (no por un humano)
            if msg.from_api:
                logger.debug(f"Mensaje fromApi ignorado: {msg.telefono}")
                continue

            # ── Números excluidos de respuestas automáticas ──
            telefono_limpio = msg.telefono.replace("@s.whatsapp.net", "").replace("+", "")
            if telefono_limpio in TELEFONOS_EXCLUIDOS:
                logger.info(f"Teléfono {msg.telefono} está excluido de respuesta automática, no responder")
                continue

            # ── Comandos del administrador ──
            global bot_activo
            if telefono_limpio == ADMIN_PHONE or msg.telefono == ADMIN_PHONE:
                comando = msg.texto.strip().upper()
                if comando == "PAUSA BOT":
                    bot_activo = False
                    await _enviar_registrando(msg.telefono, "⏸️ Bot pausado. Los mensajes no serán respondidos automáticamente.")
                    continue
                elif comando == "ACTIVAR BOT":
                    bot_activo = True
                    await _enviar_registrando(msg.telefono, "▶️ Bot activado. Olivia vuelve a responder automáticamente.")
                    continue

            # ── Si el bot está pausado, ignorar mensajes ──
            if not bot_activo:
                continue

            # ── Mensajes del grupo interno (técnicos reportando) ──
            # Normalizar formato: Z-API usa "123-group", Whapi usa "123@g.us"
            telefono_norm = msg.telefono.replace("-group", "").replace("@g.us", "")
            grupo_norm = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
            if telefono_norm == grupo_norm and grupo_norm:
                await guardar_mensaje_grupo(
                    msg.telefono,
                    msg.nombre_remitente,
                    msg.texto,
                    mensaje_id=msg.mensaje_id or msg.message_id,
                    reference_message_id=msg.reference_message_id,
                    texto_citado=msg.texto_citado,
                )

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
                await _enviar_registrando(msg.telefono, aviso)
                continue

            # ── Chequeo de intervención humana (solo chats 1-a-1) ──
            telefono_norm_iv = msg.telefono.replace("-group", "").replace("@g.us", "")
            grupo_norm_iv = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
            if not (telefono_norm_iv == grupo_norm_iv and grupo_norm_iv):
                if await hay_intervencion_reciente(msg.telefono):
                    logger.info(f"Mensaje de {msg.telefono} ignorado: intervención humana reciente")
                    continue

            # Si el cliente cita un mensaje no enviado por Olivia → intervención humana manual
            # (Z-API no envía webhook del mensaje humano, pero sí del reply del cliente)
            if msg.reference_message_id and msg.reference_message_id not in mensajes_enviados_por_bot:
                await marcar_intervencion_humana(msg.telefono)
                tarea_ref = tareas_pendientes.pop(msg.telefono, None)
                if tarea_ref and not tarea_ref.done():
                    tarea_ref.cancel()
                mensajes_pendientes.pop(msg.telefono, None)
                logger.info(f"Cliente {msg.telefono} respondió citando mensaje humano/manual; Olivia queda silenciada")
                continue

            # Deduplicar por message_id (protege contra webhooks duplicados de Z-API)
            if msg.message_id:
                if msg.message_id in mensajes_webhook_procesados:
                    logger.debug(f"Webhook duplicado ignorado: {msg.message_id}")
                    continue
                mensajes_webhook_procesados.add(msg.message_id)
                if len(mensajes_webhook_procesados) > _MAX_IDS_PROCESADOS:
                    mensajes_webhook_procesados.clear()

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
