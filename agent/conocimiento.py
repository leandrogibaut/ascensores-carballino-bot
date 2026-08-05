# agent/conocimiento.py — Normalización silenciosa de direcciones conocidas + clientes registrados

import os
import re
import csv
import io
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
                "Si el cliente ya aclaró que hay alguien, registrar sin preguntar."
            )
        else:
            lineas.append(f"- NO pedir quién abre ni horario, está dentro del horario registrado ({horario})")

    lineas.append(
        "- Si el mensaje describe una falla o reclamo técnico suficiente → "
        f"completar [SOLICITUD_COMPLETA] con: {direccion}. {{falla}}. {quien_abre}. "
        "Responder al cliente: \"Perfecto, el reclamo quedó registrado.\""
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


# ── Contactos CSV (fuente primaria) ─────────────────────────────────────────

_contactos_csv: dict[str, list[dict]] | None = None


def cargar_contactos_csv() -> dict[str, list[dict]]:
    """Lee config/contactos_olivia.csv. Resultado cacheado en memoria."""
    global _contactos_csv
    if _contactos_csv is not None:
        return _contactos_csv

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(raiz, "config", "contactos_olivia.csv")

    try:
        with open(ruta, encoding="utf-8-sig") as f:
            contenido = f.read()
    except FileNotFoundError:
        logger.error(f"contactos_csv: archivo no encontrado — {ruta}")
        _contactos_csv = {}
        return _contactos_csv

    primera_linea = contenido.split("\n")[0]
    sep = ";" if primera_linea.count(";") >= primera_linea.count(",") else ","

    _ACTIVOS = {"sí", "si", "yes", "true", "1"}
    resultado: dict[str, list[dict]] = {}
    total = 0

    reader = csv.DictReader(io.StringIO(contenido), delimiter=sep)
    for fila in reader:
        if fila.get("activo", "").strip().lower() not in _ACTIVOS:
            continue
        tel = fila.get("telefono_normalizado", "").strip()
        if not tel:
            continue
        resultado.setdefault(tel, []).append(fila)
        total += 1

    _contactos_csv = resultado
    logger.info(f"contactos_csv: {total} filas activas, {len(resultado)} teléfonos únicos")
    return _contactos_csv


def buscar_contacto_csv(telefono: str) -> dict | None:
    """Busca un contacto en el CSV por teléfono normalizado.

    Retorna:
      None                                   — no encontrado
      {"tipo": "unica", "fila": dict}        — 1 dirección activa
      {"tipo": "multi", "filas": list[dict]} — N direcciones activas
    """
    if not telefono:
        return None
    tel_norm = telefono
    for sufijo in ("@s.whatsapp.net", "@c.us", "@lid"):
        tel_norm = tel_norm.replace(sufijo, "")
    tel_norm = tel_norm.lstrip("+").replace(" ", "").strip()

    contactos = cargar_contactos_csv()
    filas = contactos.get(tel_norm)
    if not filas:
        return None

    dirs_unicas = list(dict.fromkeys(
        f["direccion_reclamo"].strip() for f in filas
        if f.get("direccion_reclamo", "").strip()
    ))

    if len(dirs_unicas) <= 1:
        return {"tipo": "unica", "fila": filas[0]}
    return {"tipo": "multi", "filas": filas}


def detectar_direccion_en_mensaje(texto: str, filas: list[dict]) -> dict | None:
    """Detecta si el texto del mensaje menciona una dirección de la lista.

    Candidatos válidos: direccion_reclamo y alias por fila.
    Aliases compartidos por 2+ filas son grupales (ej: "Algodonera", "guardia")
    y se excluyen — no alcanzan para identificar una dirección puntual.
    grupo_cliente_adm no se usa como candidato.
    Retorna la primera fila que matchea, o None.
    """
    if not texto or not filas:
        return None

    texto_norm = normalizar_texto(texto)

    # Contar en cuántas filas aparece cada candidato normalizado
    frecuencia: dict[str, int] = {}
    for fila in filas:
        vistos_en_fila: set[str] = set()
        for raw in [
            fila.get("direccion_reclamo", ""),
            *fila.get("alias", "").split(","),
        ]:
            raw = raw.strip()
            if not raw:
                continue
            n = normalizar_texto(raw)
            if n and n not in vistos_en_fila:
                frecuencia[n] = frecuencia.get(n, 0) + 1
                vistos_en_fila.add(n)

    # Candidatos que aparecen en 2+ filas son grupales → excluir del match
    grupales = {k for k, v in frecuencia.items() if v > 1}

    for fila in filas:
        candidatos: list[str] = []

        dir_reclamo = fila.get("direccion_reclamo", "").strip()
        if dir_reclamo:
            candidatos.append(dir_reclamo)

        alias_raw = fila.get("alias", "").strip()
        if alias_raw:
            candidatos.extend(a.strip() for a in alias_raw.split(",") if a.strip())

        for candidato in candidatos:
            if not candidato:
                continue
            candidato_norm = normalizar_texto(candidato)
            if candidato_norm in grupales:
                continue
            if candidato_norm in texto_norm:
                return fila

    return None


def construir_contexto_desde_csv(fila: dict, hora_actual: "datetime | None" = None) -> str:
    """Construye el bloque de contexto para el LLM a partir de una fila del CSV."""
    nombre_contacto = fila.get("nombre_contacto", "").strip()
    tipo_contacto = fila.get("tipo_contacto", "").strip()
    grupo_cliente_adm = fila.get("grupo_cliente_adm", "").strip()
    direccion_reclamo = fila.get("direccion_reclamo", "").strip()
    sector_torre = fila.get("sector_torre", "").strip()
    quien_abre = fila.get("quien_abre", "").strip()
    horario = fila.get("horario", "").strip()
    guardia_24hs = fila.get("guardia_24hs", "").strip().lower() in ("true", "sí", "si", "yes", "1")
    preguntar_fuera = fila.get("preguntar_fuera_de_horario", "").strip().lower() in ("true", "sí", "si", "yes", "1")
    observaciones = fila.get("observaciones", "").strip()
    equipos = fila.get("equipos", "").strip()

    direccion_completa = f"{direccion_reclamo} {sector_torre}".strip() if sector_torre else direccion_reclamo

    if grupo_cliente_adm and grupo_cliente_adm != direccion_reclamo:
        direccion_para_tag = f"{grupo_cliente_adm} — {direccion_completa}"
    else:
        direccion_para_tag = direccion_completa

    label_contacto = nombre_contacto
    if tipo_contacto:
        label_contacto += f" ({tipo_contacto})"

    lineas = [
        "## CLIENTE REGISTRADO",
        "Este mensaje proviene de un número registrado. Usá estos datos precargados y NO los pidas al cliente:",
        f"- Contacto: {label_contacto}",
    ]

    if grupo_cliente_adm and grupo_cliente_adm != direccion_reclamo:
        lineas.append(f"- Grupo/adm: {grupo_cliente_adm}")

    lineas += [
        f"- Dirección de reclamo: {direccion_completa}",
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
        if preguntar_fuera and hora_actual:
            fuera_de_horario = hora_actual.hour >= 19 or hora_actual.hour < 8

        if fuera_de_horario:
            lineas.append(
                f"- REGLA ESPECIAL: El mensaje llegó fuera del horario operativo ({horario}). "
                "Preguntá si hay alguien disponible para recibir al técnico antes de emitir [SOLICITUD_COMPLETA]. "
                "Si el cliente ya aclaró que hay alguien, registrar sin preguntar."
            )
        else:
            lineas.append(f"- NO pedir quién abre ni horario, está dentro del horario registrado ({horario})")

    lineas.append(
        "- Si el mensaje describe una falla o reclamo técnico suficiente → "
        f"completar [SOLICITUD_COMPLETA] con: {direccion_para_tag}. {{falla}}. {quien_abre}. "
        "Responder al cliente: \"Perfecto, el reclamo quedó registrado.\""
    )
    lineas.append(
        "- Si el mensaje es ambiguo (saludo, 'necesito un técnico', 'pueden venir', etc.) → "
        "pedir solo: \"Buen día. ¿Cuál es la falla técnica o reclamo?\""
    )

    if equipos:
        lineas.append(f"- Equipos en el edificio: {equipos}")
    if observaciones:
        lineas.append(f"- Observación: {observaciones}")

    return "\n".join(lineas)


def construir_contexto_multi_sin_match(filas: list[dict]) -> str:
    """Contexto para contacto multi-dirección cuando el mensaje no menciona dirección puntual."""
    nombre_contacto = filas[0].get("nombre_contacto", "").strip() if filas else ""

    dirs_vistas: list[str] = []
    for fila in filas:
        d = fila.get("direccion_reclamo", "").strip()
        s = fila.get("sector_torre", "").strip()
        full = f"{d} {s}".strip() if s else d
        if full and full not in dirs_vistas:
            dirs_vistas.append(full)

    lineas = [
        "## CONTACTO REGISTRADO — MÚLTIPLES DIRECCIONES",
        f"Este número ({nombre_contacto}) está asociado a varias direcciones. "
        "El mensaje NO menciona dirección específica.",
        "Pedí SOLO: \"Buen día. ¿De qué dirección es el reclamo?\"",
        "NO adivines la dirección.",
        "",
        "Direcciones posibles:",
    ]
    for d in dirs_vistas:
        lineas.append(f"- {d}")

    return "\n".join(lineas)
