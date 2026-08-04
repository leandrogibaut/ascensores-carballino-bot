"""Horario determinista para la atención automática de Olivia."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays


logger = logging.getLogger("agentkit")

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
INICIO_ATENCION_HUMANA = time(8, 0)
FIN_ATENCION_HUMANA = time(18, 0)


@lru_cache(maxsize=8)
def _feriados_del_anio(anio: int):
    """Calendario nacional argentino, generado localmente y sin depender de una API."""
    return holidays.country_holidays("AR", years=[anio], language="es")


def nombre_feriado(fecha: date) -> str:
    return str(_feriados_del_anio(fecha.year).get(fecha, ""))


def estado_atencion_olivia(ahora: datetime | None = None) -> dict[str, str | bool]:
    """Indica si Olivia debe responder en este instante y explica el motivo."""
    instante = ahora or datetime.now(TZ_AR)
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=TZ_AR)
    else:
        instante = instante.astimezone(TZ_AR)

    fecha = instante.date()
    hora = instante.time().replace(tzinfo=None)
    feriado = nombre_feriado(fecha)

    if feriado:
        habilitada = True
        motivo = f"feriado: {feriado}"
    elif instante.weekday() >= 5:
        habilitada = True
        motivo = "fin_de_semana"
    elif INICIO_ATENCION_HUMANA <= hora < FIN_ATENCION_HUMANA:
        habilitada = False
        motivo = "horario_de_atencion_humana"
    else:
        habilitada = True
        motivo = "fuera_del_horario_de_oficina"

    return {
        "respuestas_automaticas": habilitada,
        "motivo": motivo,
        "hora_local": instante.isoformat(timespec="seconds"),
    }


def olivia_debe_atender(ahora: datetime | None = None) -> bool:
    return bool(estado_atencion_olivia(ahora)["respuestas_automaticas"])
