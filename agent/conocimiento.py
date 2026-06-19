# agent/conocimiento.py — Normalización silenciosa de direcciones conocidas + clientes registrados

import os
import re
import unicodedata
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")

_clientes: list[dict] | None = None  # cache en memoria tras primera carga


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin tildes, sin espacios múltiples."""
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def cargar_clientes_direcciones() -> list[dict]:
    """Lee config/clientes_direcciones.yaml. Resultado cacheado en memoria."""
    global _clientes
    if _clientes is not None:
        return _clientes

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(raiz, "config", "clientes_direcciones.yaml")

    try:
        with open(ruta, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _clientes = data.get("clientes", [])
        logger.info(f"conocimiento: {len(_clientes)} direcciones cargadas")
    except FileNotFoundError:
        logger.error(f"conocimiento: archivo no encontrado — {ruta}")
        _clientes = []
    except yaml.YAMLError as e:
        logger.error(f"conocimiento: error leyendo YAML — {e}")
        _clientes = []

    return _clientes


_clientes_registrados: dict | None = None


def cargar_clientes_registrados() -> dict:
    """Lee config/clientes_registrados.yaml. Resultado cacheado en memoria."""
    global _clientes_registrados
    if _clientes_registrados is not None:
        return _clientes_registrados

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(raiz, "config", "clientes_registrados.yaml")

    try:
        with open(ruta, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _clientes_registrados = data.get("clientes_registrados", {})
        logger.info(f"clientes_registrados: {len(_clientes_registrados)} números cargados")
    except FileNotFoundError:
        logger.warning(f"clientes_registrados: archivo no encontrado — {ruta}")
        _clientes_registrados = {}
    except yaml.YAMLError as e:
        logger.error(f"clientes_registrados: error leyendo YAML — {e}")
        _clientes_registrados = {}

    return _clientes_registrados


def buscar_cliente_registrado(telefono: str) -> dict | None:
    """Busca un cliente por número de teléfono normalizado (549...). Retorna dict o None."""
    if not telefono:
        return None
    tel_norm = telefono
    for sufijo in ("@s.whatsapp.net", "@c.us", "@lid"):
        tel_norm = tel_norm.replace(sufijo, "")
    tel_norm = tel_norm.lstrip("+").replace(" ", "").strip()
    clientes = cargar_clientes_registrados()
    return clientes.get(tel_norm)


def construir_contexto_cliente_registrado(cliente: dict, hora_actual: datetime | None = None) -> str:
    """Construye el bloque de contexto para inyectar en el system prompt."""
    cliente_nombre = cliente.get("cliente", "")
    direccion = cliente.get("direccion", "")
    quien_abre = cliente.get("quien_abre", "")
    horario = cliente.get("horario", "")
    guardia_24hs = cliente.get("guardia_24hs", False)
    preguntar_fuera_de_horario = cliente.get("preguntar_fuera_de_horario", False)
    observaciones = cliente.get("observaciones", "")

    lineas = [
        "## CLIENTE REGISTRADO",
        "Este mensaje proviene de un número registrado. Usá estos datos precargados y NO los pidas al cliente:",
        f"- Cliente: {cliente_nombre}",
        f"- Dirección: {direccion}",
        f"- Quién abre: {quien_abre}",
        f"- Horario: {horario}",
    ]

    if hora_actual:
        lineas.append(f"- Hora actual: {hora_actual.strftime('%H:%M')}")

    lineas.append("")
    lineas.append("INSTRUCCIONES PARA ESTE MENSAJE:")
    lineas.append("- NO pedir dirección (ya disponible)")

    if guardia_24hs:
        lineas.append("- NO pedir quién abre ni horario (guardia 24 hs activa)")
    else:
        fuera_de_horario = False
        if preguntar_fuera_de_horario and hora_actual:
            hora_num = hora_actual.hour
            fuera_de_horario = hora_num >= 19 or hora_num < 8

        if fuera_de_horario:
            lineas.append(
                f"- REGLA ESPECIAL: El mensaje llegó fuera del horario operativo ({horario}). "
                "Preguntá si hay alguien disponible para recibir al técnico antes de emitir [SOLICITUD_COMPLETA]. "
                "Si el cliente ya aclaró que hay alguien, derivar sin preguntar."
            )
        else:
            lineas.append(f"- NO pedir quién abre ni horario, está dentro del horario registrado ({horario})")

    lineas.append(
        "- Si el mensaje describe una falla o reclamo técnico suficiente → "
        f"completar [SOLICITUD_COMPLETA] con: {direccion}. {{falla}}. {quien_abre}. "
        "Responder al cliente: \"Perfecto, ya lo paso a los técnicos.\""
    )
    lineas.append(
        "- Si el mensaje es ambiguo (saludo, 'necesito un técnico', 'pueden venir', 'me reclamaron', etc.) → "
        "pedir solo: \"Buen día. ¿Cuál es la falla técnica o reclamo?\""
    )

    if observaciones:
        lineas.append(f"- Observación: {observaciones}")

    return "\n".join(lineas)


def buscar_cliente_por_texto(texto: str) -> dict | None:
    """
    Busca en el texto si aparece la dirección o algún alias de un cliente conocido.
    Ignora mayúsculas, tildes y espacios múltiples.
    Retorna el dict completo del cliente si hay match, None si no.
    El match es por substring: el alias normalizado debe aparecer dentro del texto normalizado.
    """
    if not texto or not texto.strip():
        return None

    texto_norm = normalizar_texto(texto)
    clientes = cargar_clientes_direcciones()

    for cliente in clientes:
        candidatos: list[str] = list(cliente.get("aliases", []))

        # Incluir direccion directamente por si falta en aliases
        direccion = cliente.get("direccion", "")
        if direccion and direccion not in candidatos:
            candidatos.append(direccion)

        for alias in candidatos:
            if not alias:
                continue
            alias_norm = normalizar_texto(alias)
            if alias_norm and alias_norm in texto_norm:
                return cliente

    return None
