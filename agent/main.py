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
import unicodedata
import logging
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    guardar_solicitud,
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
from agent.conocimiento import buscar_cliente_por_texto, buscar_cliente_registrado, construir_contexto_cliente_registrado
from agent.providers import obtener_proveedor
from agent.providers.zapi import es_intervencion_humana, extraer_numero_conversacion, normalizar_numero_whatsapp
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

AVISO_EMERGENCIA = (
    "\n\nAnte cualquier problema o emergencia, comuníquese directamente "
    "por llamada telefónica común al 4301-3967 o al 1565024510. "
    "No por llamada de WhatsApp."
)


def asegurar_aviso_emergencia(texto: str) -> str:
    if "4301-3967" in texto or "1565024510" in texto:
        return texto
    return texto.strip() + AVISO_EMERGENCIA


_CIERRES: frozenset[str] = frozenset({
    "ok", "okey", "oki", "okie", "dale", "va", "va bien",
    "gracias", "muchas gracias", "mil gracias", "grax",
    "perfecto", "listo", "bárbaro", "barbaro",
    "genial", "excelente", "buenísimo", "buenisimo",
    "te agradezco", "te agradezco mucho",
    "de nada", "no hay de qué", "no hay de que",
    "joya", "entendido", "entendí", "entendi",
    "copado", "bien", "muy bien", "todo bien",
    "👍", "🙏",
})

_PALABRAS_NUEVA_INFO: list[str] = [
    "ascensor", "elevador", "falla", "problema", "roto", "no funciona",
    "dirección", "direccion", "piso", "depto", "edificio",
    "factura", "pago", "contrato", "presupuesto", "abono",
    "urgente", "emergencia", "atrapado", "encerrado",
]


def es_cierre_conversacion(texto: str) -> bool:
    """Retorna True si el mensaje es solo un cierre o agradecimiento sin información nueva."""
    if "?" in texto:
        return False
    texto_lower = texto.strip().lower()
    for palabra in _PALABRAS_NUEVA_INFO:
        if palabra in texto_lower:
            return False
    lineas = [l.strip() for l in texto_lower.split("\n") if l.strip()]
    if not lineas:
        return False
    return all(
        re.sub(r"[¡!¿.,;:'\"]+", "", linea).strip() in _CIERRES
        for linea in lineas
    )


def _normalizar_texto_dedup(texto: str) -> str:
    """Normaliza texto para deduplicación: sin tildes, minúsculas, sin puntuación, sin espacios extra."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^\w\s]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _ya_procesado_por_contenido(telefono: str, texto: str, ventana_seg: int = 120) -> bool:
    """Retorna True si el mismo número mandó el mismo texto dentro de la ventana de tiempo."""
    telefono_norm = normalizar_numero_whatsapp(telefono)
    texto_norm = _normalizar_texto_dedup(texto)
    clave = (telefono_norm, texto_norm)
    ahora = datetime.utcnow()
    ultimo = _contenidos_procesados.get(clave)
    if ultimo and (ahora - ultimo).total_seconds() < ventana_seg:
        logger.info(f"DUPLICADO POR CONTENIDO: no se responde | {telefono_norm} | '{texto[:60]}'")
        return True
    _contenidos_procesados[clave] = ahora
    if len(_contenidos_procesados) > _MAX_CONTENIDOS_PROCESADOS:
        _contenidos_procesados.clear()
    return False


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
    """Envía a las 20:00hs un resumen legible de los mensajes del grupo al número configurado."""
    try:
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        hoy = datetime.now(tz).date()
        fecha_str = hoy.strftime("%d/%m/%Y")

        mensajes = await obtener_mensajes_grupo_por_fecha(hoy)

        if not mensajes:
            texto = (
                f"Resumen de reclamos del {fecha_str}\n\n"
                f"No se registraron mensajes en el grupo Reclamos Ascensores Carballino."
            )
        else:
            lineas = [f"Resumen de reclamos del {fecha_str}", ""]
            for i, m in enumerate(mensajes, 1):
                hora = m.timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%H:%M")
                remitente = m.nombre_remitente or m.telefono_remitente or "Desconocido"
                texto_msg = (m.texto or "").strip()
                lineas.append(f"{i}. {hora} - {remitente}")
                if texto_msg:
                    lineas.append(f"{texto_msg}")
            lineas.append("")
            lineas.append(f"Total de mensajes procesados: {len(mensajes)}.")
            texto = "\n".join(lineas)

        resultado = await proveedor.enviar_mensaje(REPORTE_DIARIO_TELEFONO, texto)
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
_contenidos_procesados: dict[tuple, datetime] = {}
_MAX_CONTENIDOS_PROCESADOS = 2000

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
    if await conversacion_silenciada(telefono):
        logger.info(f"CANCELA ENVÍO: conversación silenciada | {normalizar_numero_whatsapp(telefono)}")
        return None
    msg_id = await proveedor.enviar_mensaje(telefono, mensaje)
    if msg_id:
        mensajes_enviados_por_bot.add(msg_id)
        if len(mensajes_enviados_por_bot) > _MAX_IDS_PROCESADOS:
            mensajes_enviados_por_bot.clear()
    return msg_id


async def conversacion_silenciada(numero: str) -> bool:
    """Retorna True si la conversación está silenciada por intervención humana reciente."""
    numero_norm = normalizar_numero_whatsapp(numero)
    logger.debug(f"SILENCIO CONSULTADO PARA: {numero_norm}")
    return await hay_intervencion_reciente(numero_norm)


async def silenciar_conversacion(numero: str, horas: int = 6, motivo: str = ""):
    """Marca silencio en Postgres, cancela tareas pendientes y limpia estado de conversación."""
    original = numero
    numero_norm = normalizar_numero_whatsapp(numero)
    logger.warning(f"INTERVENCIÓN HUMANA DETECTADA | motivo: {motivo}")
    logger.info(f"NÚMERO ORIGINAL: {original}")
    logger.info(f"NÚMERO NORMALIZADO: {numero_norm}")
    await marcar_intervencion_humana(numero_norm)
    logger.info(f"SILENCIO GUARDADO PARA: {numero_norm}")
    limpiar_estado_conversacion(original)
    tarea = tareas_pendientes.pop(original, None)
    if tarea and not tarea.done():
        tarea.cancel()
    mensajes_pendientes.pop(original, None)
    silencio_hasta = (datetime.utcnow() + timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M UTC")
    logger.warning(f"Silencio {horas}hs para {numero_norm} | hasta: {silencio_hasta} | motivo: {motivo}")


async def procesar_mensaje_cliente(telefono: str, texto: str):
    """Procesa un mensaje de cliente y genera respuesta de Olivia."""
    import asyncio
    historial = await obtener_historial(telefono)

    tel_norm = normalizar_numero_whatsapp(telefono)
    cliente_reg = buscar_cliente_registrado(tel_norm)
    contexto_cliente = ""
    if cliente_reg:
        hora_actual = datetime.now(TZ_AR)
        contexto_cliente = construir_contexto_cliente_registrado(cliente_reg, hora_actual)
        logger.info(
            f"CLIENTE REGISTRADO DETECTADO | {tel_norm} | "
            f"{cliente_reg.get('nombre', '')} | {cliente_reg.get('direccion', '')}"
        )

    respuesta = await generar_respuesta(texto, historial, contexto_cliente=contexto_cliente)

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
            resultado_grupo = await notificar_grupo_solicitud(telefono, resumen_texto, proveedor, solicitud_id)
            if resultado_grupo:
                logger.info(f"RECLAMO DERIVADO A GRUPO | solicitud #{solicitud_id} | {telefono}")
                logger.info(f"SILENCIO POR RECLAMO DERIVADO | {telefono}")
                await silenciar_conversacion(telefono, horas=6, motivo="reclamo_derivado_grupo")
            else:
                logger.error(f"ERROR ENVÍO GRUPO — notificación falló para solicitud #{solicitud_id}")
        respuesta = re.sub(r'\[SOLICITUD_COMPLETA:.+?\]', '', respuesta, flags=re.DOTALL).strip()
        if not respuesta:
            respuesta = "Perfecto, ya registramos el reclamo y lo derivamos al equipo técnico. Muchas gracias."

    if re.search(r'\[DERIVAR_ADMIN\]', respuesta):
        marcar_estado_conversacion(telefono, "pendiente_admin")
        respuesta = re.sub(r'\[DERIVAR_ADMIN\]', '', respuesta).strip()
        logger.info(f"Consulta administrativa de {telefono} derivada a equipo humano")

    if not respuesta or not respuesta.strip():
        logger.error(f"Respuesta vacía generada para {telefono}. No se envía mensaje.")
        return

    respuesta = asegurar_aviso_emergencia(respuesta)

    if await conversacion_silenciada(telefono):
        logger.info(f"NO RESPONDE: conversación silenciada (detectada antes de enviar) | {telefono}")
        return

    await guardar_mensaje(telefono, "user", texto)
    await guardar_mensaje(telefono, "assistant", respuesta)
    await _enviar_registrando(telefono, respuesta)
    logger.info(f"Respuesta a {telefono}: {respuesta}")


async def procesar_acumulados(telefono: str):
    debounce = DEBOUNCE_SEGUNDOS if await tiene_mensajes_recientes(telefono) else DEBOUNCE_NUEVO_SEGUNDOS
    await asyncio.sleep(debounce)
    textos = mensajes_pendientes.pop(telefono, [])
    tareas_pendientes.pop(telefono, None)
    if not textos:
        return
    texto_combinado = "\n".join(textos)
    logger.info(f"Procesando {len(textos)} mensaje(s) de {telefono}: {texto_combinado}")

    if await conversacion_silenciada(telefono):
        logger.info(f"NO RESPONDE: conversación silenciada | {telefono}")
        return

    if obtener_estado_conversacion(telefono) == "pendiente_admin":
        logger.info(f"{telefono} en pendiente_admin — esperando intervención humana")
        return

    if es_cierre_conversacion(texto_combinado):
        logger.info(f"CIERRE DETECTADO: no se responde | {telefono} | '{texto_combinado}'")
        return

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
        # ── Early return: detectar intervención humana en el payload crudo ──
        # Se hace ANTES de parsear para no depender del tipo de webhook de Z-API.
        payload = await request.json()
        if es_intervencion_humana(payload) and not payload.get("fromApi") and not payload.get("isGroup"):
            numero = extraer_numero_conversacion(payload)
            if numero:
                await silenciar_conversacion(numero, horas=6, motivo="intervencion_humana")
                return {"ok": True, "silenciado": True}

        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            logger.info(
                f"WEBHOOK | de: {msg.telefono} | fromMe: {msg.es_propio} "
                f"| fromApi: {getattr(msg, 'from_api', False)} "
                f"| senderName: {getattr(msg, 'nombre_remitente', '')} "
                f"| messageId: {getattr(msg, 'message_id', '')}"
            )

            # Fallback: mensajes propios que llegaron a parsear (bot o grupo)
            if msg.es_propio:
                if not msg.from_api:
                    es_del_bot = bool(msg.message_id and msg.message_id in mensajes_enviados_por_bot)
                    if not es_del_bot:
                        telefono_norm_p = msg.telefono.replace("-group", "").replace("@g.us", "")
                        grupo_norm_p = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
                        es_grupo_p = telefono_norm_p == grupo_norm_p and bool(grupo_norm_p)
                        if not es_grupo_p:
                            await silenciar_conversacion(msg.telefono, horas=6, motivo="intervencion_humana_fallback")
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
                elif msg.texto.strip().lower().startswith("/silencio "):
                    numero_raw = msg.texto.strip().split(" ", 1)[1].strip()
                    numero_norm = normalizar_numero_whatsapp(numero_raw)
                    await silenciar_conversacion(numero_norm, horas=6, motivo="comando_manual")
                    await _enviar_registrando(msg.telefono, f"Listo, silencio activado por 6 horas para {numero_norm}.")
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

            # ── Respuesta automática a audio sin transcripción ──
            if msg.texto == "__audio_sin_transcripcion__":
                await _enviar_registrando(
                    msg.telefono,
                    "No pude escuchar bien el audio. ¿Me lo podés mandar por escrito?"
                )
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
                if await conversacion_silenciada(msg.telefono):
                    logger.info(f"NO RESPONDE: conversación silenciada | {msg.telefono}")
                    continue

            # Si el cliente cita un mensaje no enviado por Olivia → intervención humana manual
            # (Z-API no envía webhook del mensaje humano, pero sí del reply del cliente)
            if msg.reference_message_id and msg.reference_message_id not in mensajes_enviados_por_bot:
                await silenciar_conversacion(msg.telefono, horas=6, motivo="intervencion_humana_reference")
                continue

            # Deduplicar por message_id (protege contra webhooks duplicados de Z-API)
            if msg.message_id:
                if msg.message_id in mensajes_webhook_procesados:
                    logger.debug(f"Webhook duplicado ignorado: {msg.message_id}")
                    continue
                mensajes_webhook_procesados.add(msg.message_id)
                if len(mensajes_webhook_procesados) > _MAX_IDS_PROCESADOS:
                    mensajes_webhook_procesados.clear()

            # Deduplicar por contenido (Z-API puede reenviar mismo texto con distinto messageId)
            if _ya_procesado_por_contenido(msg.telefono, msg.texto):
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
