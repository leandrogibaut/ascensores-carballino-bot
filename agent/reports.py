# agent/reports.py — Generación de reportes sin dependencias de IA ni proveedor

from datetime import datetime, date
from zoneinfo import ZoneInfo

from agent.memory import obtener_solicitudes_por_fecha


async def generar_reporte_diario_preview(fecha: date | None = None) -> str:
    """Genera el texto del reporte diario sin enviar nada."""
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    hoy = fecha or datetime.now(tz).date()
    fecha_str = hoy.strftime("%d/%m/%Y")

    solicitudes = await obtener_solicitudes_por_fecha(hoy)

    resueltos  = [s for s in solicitudes if s.estado == "resuelto"]
    en_curso   = [s for s in solicitudes if s.estado == "pendiente_con_nota"]
    pendientes = [s for s in solicitudes if s.estado == "pendiente"]

    lineas = [f"Reporte diario - {fecha_str}", ""]

    lineas.append("Reclamos solucionados:")
    if resueltos:
        for s in resueltos:
            nota = f"Técnico indicó: {s.notas_tecnico}" if s.notas_tecnico else "Solucionado."
            lineas.append(f"- {s.direccion}, {s.tipo}. {nota}")
    else:
        lineas.append("- Ninguno.")

    lineas.append("")
    lineas.append("Reclamos en curso:")
    if en_curso:
        for s in en_curso:
            nota = f"Técnico indicó: {s.notas_tecnico}" if s.notas_tecnico else "Sin nota."
            lineas.append(f"- {s.direccion}, {s.tipo}. {nota}")
    else:
        lineas.append("- Ninguno.")

    lineas.append("")
    lineas.append("Reclamos pendientes:")
    if pendientes:
        for s in pendientes:
            lineas.append(f"- {s.direccion}, {s.tipo}. Sin respuesta técnica registrada.")
    else:
        lineas.append("- Ninguno.")

    return "\n".join(lineas)
