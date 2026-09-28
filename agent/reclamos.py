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

# Personas en riesgo dentro de la cabina. Se evalúa sobre texto normalizado
# (minúsculas, sin tildes) y solo SUMA casos a _EMERGENCIA_CRITICA_RE: nunca
# quita uno. Ante la duda se prefiere un falso positivo (respuesta de guardia)
# a un falso negativo (persona atrapada tratada como reclamo común).
_PERSONA = (
    r"(?:alguien|gente|personas?|nen[ea]s?|chic[oa]s?|nin[oa]s?|pib[ea]s?|"
    r"bebes?|senor(?:a|es|as)?|abuel[oa]s?|vecin[oa]s?|mama|papa|hij[oa]s?|"
    r"herman[oa]s?|hombres?|mujer(?:es)?|viej[oa]s?)"
)
_PERSONA_RE = re.compile(rf"\b{_PERSONA}\b")
# "estoy adentro" no cuenta si se refiere al edificio u otro ambiente, ni si es
# un cierre ("ya estoy adentro, gracias").
_ADENTRO_DE_OTRO_LUGAR = (
    r"(?!\s+(?:del|de\s+la)\s+"
    r"(?:edificio|departamento|depto|casa|oficina|local|hall|palier)\b)"
    r"(?![\s,.!]*gracias)"
)
_PERSONA_EN_RIESGO_RE = re.compile(
    r"\b(?:"
    r"encerrad[oa]s?|atrapad[oa]s?"
    r"|no\s+(?:puede|pueden|puedo|podemos|pudo|pudieron|logra|logran)\s+salir"
    r"|sin\s+poder\s+salir"
    r"|qued\w*(?:\s+\S+){0,5}?\s+adentro"
    rf"|(?:estoy|estamos|sigo|seguimos)\s+adentro{_ADENTRO_DE_OTRO_LUGAR}"
    rf"|{_PERSONA}(?:\s+\S+){{0,4}}?\s+adentro"
    r"|entre\s+(?:dos\s+|los\s+)?pisos?|entrepisos?"
    r"|entre\s+el\s+\d+\w*\s+y\s+(?:el\s+)?\d+\w*"
    r")\b"
)
# Verbos de "ascensor detenido". Solo cuentan con una persona en la misma frase.
# trab(?!aj) evita "trabaja/trabajando".
_DETENIDO_RE = re.compile(
    r"\b(?:trab(?!aj)\w*|se\s+paro|parad[oa]s?|se\s+clav\w*|se\s+detuv\w*|detenid[oa]s?)\b"
)

# Riesgo de incendio o eléctrico descripto de forma coloquial. Mismo criterio:
# texto normalizado y solo suma casos.
_RIESGO_FUEGO_RE = re.compile(
    r"\b(?:"
    r"(?:olor|olorcito|huele)\s+a\s+quemado"
    r"|humo|humareda"
    r"|chispa\w*|chispe\w*|chisporrot\w*"
    r")\b"
)

_CONSULTA_ESTADO_RE = re.compile(
    r"\b(quer[ií]a\s+saber|ya\s+(?:lo\s+)?pasaron|si\s+pasaron|fueron\s+a\s+ver|"
    r"consulto\s+si|alguna\s+novedad|hay\s+novedad(?:es)?|"
    r"van\s+a\s+venir|van\s+a\s+pasar|vinieron|cu[aá]ndo\s+vienen|"
    r"me\s+confirm[aá]s?\s+si\s+(?:vienen|van)|"
    r"vieron\s+(?:(?:el|los)\s+)?(?:mensaje|msj|reclamo)s?|"
    r"pudieron\s+ver|"
    r"pasaron\s+el\s+reclamo)\b",
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


def _persona_trabada(normalizado: str) -> bool:
    """Trabado, parado, se clavó o detenido solo es emergencia si en la misma
    frase hay una persona."""
    return any(
        _DETENIDO_RE.search(frase) and _PERSONA_RE.search(frase)
        for frase in re.split(r"[.!?;\n]+", normalizado)
    )


def es_emergencia_critica(texto: str) -> bool:
    texto = texto or ""
    normalizado = _normalizar(texto)
    return bool(
        _EMERGENCIA_CRITICA_RE.search(texto)
        or _PERSONA_EN_RIESGO_RE.search(normalizado)
        or _RIESGO_FUEGO_RE.search(normalizado)
        or _persona_trabada(normalizado)
    )


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


def es_seguimiento_reclamo(texto: str) -> bool:
    """Reconoce que el cliente pregunta por el estado de un reclamo previo
    ("¿van a venir?", "¿vieron los mensajes?", "¿hay novedades?"), no que está
    describiendo una falla nueva. Si el mensaje además describe una falla técnica
    clara (ej. "van a venir técnicos por el ascensor 3 que quedó parado"), no se
    considera un simple seguimiento — es_reclamo_tecnico_claro debe manejarlo.
    """
    texto = texto or ""
    return bool(_CONSULTA_ESTADO_RE.search(texto)) and not _FALLA_RE.search(texto)


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
