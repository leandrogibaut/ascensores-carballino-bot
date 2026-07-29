"""Reglas deterministas de respaldo para no perder reclamos técnicos.

El modelo sigue redactando la respuesta y extrayendo datos cuando puede. Este módulo
actúa como red de seguridad: si hay una falla técnica y una dirección identificable,
el reclamo puede derivarse aunque falte la disponibilidad para recibir al técnico.
"""

from __future__ import annotations

import re
import unicodedata


_EQUIPO_RE = re.compile(
    r"\b(ascensor(?:es)?|asc\.?|elevador(?:a|es)?|bomba(?:s)?|montacarga(?:s)?)\b",
    re.IGNORECASE,
)

_FALLA_RE = re.compile(
    r"\b(no\s+(?:funciona|anda|abre|cierra|responde|sube|baja)|"
    r"parad[oa]s?|detenid[oa]s?|fuera\s+de\s+servicio|"
    r"desnivel(?:ad[oa]s?)?|nivela\s+mal|fuera\s+de\s+nivel|"
    r"trab(?:ad[oa]|a)|falla|problema|"
    r"movimientos?\s+raros?|ruido|golpe|tiron|tirón|"
    r"encerrad[oa]s?|atrapad[oa]s?|gente\s+adentro|"
    r"se\s+(?:solto|soltó|salieron|rompio|rompió|corto|cortó)|"
    r"quemaron?|pierde\s+agua|sin\s+ascensor|"
    r"puerta|botonera|manija|tensor(?:es)?|cable(?:s)?|luces?)\b",
    re.IGNORECASE,
)

_EMERGENCIA_CRITICA_RE = re.compile(
    r"\b(persona(?:s)?\s+(?:encerrad[oa]s?|atrapad[oa]s?)|"
    r"gente\s+(?:encerrada|atrapada|adentro)|"
    r"accidente|herid[oa]s?|humo|fuego|chispas?|riesgo\s+electrico|"
    r"riesgo\s+eléctrico)\b",
    re.IGNORECASE,
)

_CONSULTA_ESTADO_RE = re.compile(
    r"\b(quer[ií]a\s+saber|ya\s+pasaron|si\s+pasaron|fueron\s+a\s+ver|"
    r"consulto\s+si|alguna\s+novedad)\b",
    re.IGNORECASE,
)

_DIRECCION_RE = re.compile(
    r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ.'-]*"
    r"(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñOo][A-Za-zÁÉÍÓÚÜÑáéíóúüñ.'-]*){0,4})"
    r"\s+(\d{2,5})\b"
)

_PREFIJOS_DESCARTABLES = {
    "hola", "buen", "buena", "buenos", "buenas", "dia", "día", "dias", "días",
    "tarde", "tardes", "noche", "noches", "soy", "somos", "de", "del", "desde",
    "aca", "acá", "en", "el", "la", "los", "las", "edificio", "direccion", "dirección",
    "mi", "nombre", "es", "escribo", "te", "paso", "encargado", "encargada", "portero", "portera",
    "intendente", "intendenta", "administrador", "administradora",
}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def es_emergencia_critica(texto: str) -> bool:
    return bool(_EMERGENCIA_CRITICA_RE.search(texto or ""))


def es_reclamo_tecnico_claro(texto: str) -> bool:
    """Evita depender del tag del modelo para reconocer una falla técnica clara."""
    texto = texto or ""
    if es_emergencia_critica(texto):
        return True
    if _CONSULTA_ESTADO_RE.search(texto) and not _FALLA_RE.search(texto):
        return False
    tiene_equipo = bool(_EQUIPO_RE.search(texto))
    tiene_falla = bool(_FALLA_RE.search(texto))
    contexto_vertical = bool(re.search(r"\b(piso|pb|planta\s+baja)\b", texto, re.IGNORECASE))
    return tiene_falla and (tiene_equipo or contexto_vertical)


def direccion_especial(texto: str) -> str:
    """Alias operativos confirmados por la empresa."""
    normalizado = _normalizar(texto or "")
    if re.search(r"\btorre\s+c\b", normalizado):
        return "Arribeños / Montañeses 3150 Torre C"
    return ""


def extraer_direccion_libre(texto: str) -> str:
    """Extrae una calle y altura cuando no existe un cliente previamente asociado."""
    especial = direccion_especial(texto)
    if especial:
        return especial

    candidatos: list[str] = []
    for match in _DIRECCION_RE.finditer(texto or ""):
        palabras = match.group(1).strip(" ,.-").split()
        while palabras and _normalizar(palabras[0]) in _PREFIJOS_DESCARTABLES:
            palabras.pop(0)
        if not palabras:
            continue
        calle = " ".join(palabras).strip()
        calle_norm = _normalizar(calle)
        if calle_norm in {"ascensor", "asc", "piso", "torre"}:
            continue
        candidatos.append(f"{calle} {match.group(2)}")

    return candidatos[-1] if candidatos else ""


def inferir_quien_abre(texto: str) -> str:
    """Reconoce que quien escribe es encargado/a, portero/a o intendente/a."""
    texto = texto or ""
    rol_match = re.search(
        r"\b(?:soy|somos)\s+(?:el\s+|la\s+)?"
        r"(encargad[oa]|porter[oa]|intendent[ea]|administrador(?:a)?)\b",
        texto,
        re.IGNORECASE,
    )
    if not rol_match:
        return ""

    nombre_match = re.search(
        r"\bmi\s+nombre\s+es\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]+)",
        texto,
        re.IGNORECASE,
    )
    rol = rol_match.group(1).lower()
    if nombre_match:
        return f"{nombre_match.group(1).title()}, {rol}"
    return f"Quien escribe ({rol})"


def crear_derivacion_respaldo(
    texto: str,
    direccion_preferida: str = "",
    quien_abre_preferido: str = "",
) -> dict[str, str | bool] | None:
    """Devuelve los datos mínimos para derivar o None si falta dirección/falla."""
    if not es_reclamo_tecnico_claro(texto):
        return None

    direccion = direccion_preferida.strip() or extraer_direccion_libre(texto)
    if not direccion:
        return None

    quien_abre = quien_abre_preferido.strip() or inferir_quien_abre(texto)
    disponibilidad_pendiente = not bool(quien_abre)
    if not quien_abre:
        quien_abre = "Disponibilidad no informada"

    falla = " ".join((texto or "").split()).strip()
    if es_emergencia_critica(texto) and not falla.upper().startswith("URGENTE"):
        falla = f"URGENTE: {falla}"
    falla_para_tag = re.sub(r"\s*[.\n]+\s*", "; ", falla).strip(" ;")

    return {
        "direccion": direccion,
        "tipo": falla[:500],
        "quien_abre": quien_abre,
        "disponibilidad_pendiente": disponibilidad_pendiente,
        "emergencia": es_emergencia_critica(texto),
        "datos_raw": f"{direccion}. {falla_para_tag[:500]}. {quien_abre}.",
    }
