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
    "Bienvenidos a Ascensores Carballino 🏢\n\n"
    "Pulse la opción correcta para ser atendido:\n\n"
    "*1* — Reclamo / Servicio Técnico\n"
    "*2* — Oficina / Administración"
)
BOTONES_MENU = [
    {"id": "RECLAMO", "label": "1 - Reclamo / Servicio Técnico"},
    {"id": "ADM",     "label": "2 - Oficina / Administración"},
]
MENSAJE_ADM = (
    "En breve se comunicarán con usted desde la oficina. "
    "Nuestro horario de atención es de lunes a viernes de 8 a 18hs."
)
MENSAJE_ADM_FUERA_HORARIO = (
    "Gracias por comunicarse con Ascensores Carballino.\n\n"
    "En este momento nos encontramos fuera del horario de atención.\n"
    "Nuestro horario es de lunes a viernes de 8 a 18hs.\n\n"
    "Puede dejarnos su consulta y le responderemos el próximo día hábil."
)

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")


def oficina_esta_disponible() -> bool:
    """Retorna True si es día hábil entre 8:00 y 18:00 hora Argentina."""
    ahora = datetime.now(TZ_AR)
    es_dia_habil = ahora.weekday() < 5  # 0=lunes … 4=viernes
    es_horario = 8 <= ahora.hour < 18
    return es_dia_habil and es_horario
# Estado del menú por teléfono: None = no visto | "esperando_menu" | "reclamo" | "administracion"
sesion_menu: dict[str, str] = {}

def formatear_resumen_solicitud(datos_raw: str) -> tuple[str, dict]:
    """Convierte el tag interno de solicitud en un párrafo corto para el grupo."""
    extraido = {}
    for clave in ("tipo", "nombre", "tel", "consorcio", "direccion", "quien_abre", "piso_depto"):
        match = re.search(rf'{clave}="([^"]*)"', datos_raw)
        if match and match.group(1):
            extraido[clave] = match.group(1)

    direccion = extraido.get("direccion", "")
    tipo = extraido.get("tipo", "")
    quien_abre = extraido.get("quien_abre", "")
    piso_depto = extraido.get("piso_depto", "")

    partes = [p for p in [direccion, tipo] if p]
    if quien_abre:
        abre = f"Abre {quien_abre}"
        if piso_depto and piso_depto.upper() != "N/A":
            abre += f" ({piso_depto})"
        partes.append(abre)

    resumen = " - ".join(partes) if partes else datos_raw
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

    # Enviar JSON con todos los mensajes del grupo del día a +5491122636490
    import json as _json
    mensajes_grupo = await obtener_mensajes_grupo_del_dia()
    if mensajes_grupo:
        payload = [
            {
                "telefono": m.telefono_remitente,
                "nombre": m.nombre_remitente,
                "texto": m.texto,
                "hora": m.timestamp.strftime("%H:%M"),
            }
            for m in mensajes_grupo
        ]
        texto_json = _json.dumps(payload, ensure_ascii=False, indent=2)
        await proveedor.enviar_mensaje("5491122636490", texto_json)
        logger.info(f"JSON de mensajes del grupo enviado a +5491122636490 ({len(payload)} mensajes)")


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
            await proveedor.enviar_menu_botones(telefono, MENSAJE_MENU, BOTONES_MENU)
            return

    if sesion_menu[telefono] == "esperando_menu":
        texto_norm = texto_combinado.strip().upper()
        if texto_norm in ("RECLAMO", "1"):
            sesion_menu[telefono] = "reclamo"
            mensaje_inicial = (
                "Hola, soy Olivia de Ascensores Carballino 💎\n\n"
                "¿De qué dirección se comunica? ¿Cuál es exactamente la falla? "
                "¿Quién puede abrir? Indicame piso y departamento así enviamos "
                "a los técnicos a solucionar el reclamo.\n\n"
                "⚠️ Si se trata de una emergencia, por favor llame por teléfono "
                "al 4301-3967 o al 1565024510."
            )
            await guardar_mensaje(telefono, "assistant", mensaje_inicial)
            await proveedor.enviar_mensaje(telefono, mensaje_inicial)
        elif texto_norm in ("ADM", "2", "ADMINISTRACION", "ADMINISTRACIÓN", "PAGOS", "OFICINA"):
            sesion_menu[telefono] = "administracion"
            await guardar_mensaje(telefono, "user", texto_combinado)
            if oficina_esta_disponible():
                await guardar_mensaje(telefono, "assistant", MENSAJE_ADM)
                await proveedor.enviar_mensaje(telefono, MENSAJE_ADM)
            else:
                await guardar_mensaje(telefono, "assistant", MENSAJE_ADM_FUERA_HORARIO)
                await proveedor.enviar_mensaje(telefono, MENSAJE_ADM_FUERA_HORARIO)
        else:
            await proveedor.enviar_menu_botones(telefono, MENSAJE_MENU, BOTONES_MENU)
        return

    if sesion_menu[telefono] == "administracion":
        msg = MENSAJE_ADM if oficina_esta_disponible() else MENSAJE_ADM_FUERA_HORARIO
        await proveedor.enviar_mensaje(telefono, msg)
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
                continue

            if not msg.texto:
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
                await guardar_mensaje_grupo(msg.telefono, msg.nombre_remitente, msg.texto)

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

            # ── Chequeo de intervención humana (solo chats 1-a-1) ──
            telefono_norm_iv = msg.telefono.replace("-group", "").replace("@g.us", "")
            grupo_norm_iv = GRUPO_INTERNO.replace("-group", "").replace("@g.us", "")
            if not (telefono_norm_iv == grupo_norm_iv and grupo_norm_iv):
                if await hay_intervencion_reciente(msg.telefono):
                    logger.info(f"Mensaje de {msg.telefono} ignorado: intervención humana reciente")
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
