#!/usr/bin/env python3
"""
Importa histórico de mensajes del grupo Reclamos Ascensores Carballino.

Lee un archivo de texto con múltiples JSON diarios (pegados tal cual o con
prefijos de exportación de WhatsApp como "[8/6/26, 5:50:05 p. m.] Alejandro:").

Uso:
    python scripts/importar_historico_reclamos.py
    python scripts/importar_historico_reclamos.py data/otro_archivo.txt
"""

import asyncio
import json
import os
import sys
from datetime import datetime, date, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.memory import inicializar_db, async_session, MensajeGrupo
from sqlalchemy import select

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
TZ_UTC = ZoneInfo("UTC")
ARCHIVO_DEFAULT = "data/historico_reclamos_whatsapp.txt"


def extraer_jsons(contenido: str) -> list[tuple[dict | None, str | None]]:
    """
    Escanea el texto y extrae todos los objetos JSON que contengan 'messages'.
    Ignora líneas de exportación de WhatsApp como "[8/6/26, 5:41] Alejandro:".
    Retorna lista de (objeto_dict | None, mensaje_error | None).
    """
    resultados = []
    decoder = json.JSONDecoder()
    pos = 0

    while pos < len(contenido):
        # Buscar el próximo '{'
        idx = contenido.find("{", pos)
        if idx == -1:
            break
        try:
            obj, offset = decoder.raw_decode(contenido, idx)
            if isinstance(obj, dict) and "messages" in obj:
                resultados.append((obj, None))
            pos = offset
        except json.JSONDecodeError as e:
            resultados.append((None, f"JSON malformado en posición {idx}: {e}"))
            pos = idx + 1

    return resultados


async def mensaje_existe(session, message_id: str) -> bool:
    if not message_id:
        return False
    result = await session.execute(
        select(MensajeGrupo).where(MensajeGrupo.mensaje_id == message_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def importar(archivo: str):
    await inicializar_db()

    ruta = Path(archivo)
    if not ruta.exists():
        print(f"ERROR: No se encontró el archivo: {ruta}")
        sys.exit(1)

    contenido = ruta.read_text(encoding="utf-8", errors="replace")
    print(f"Archivo: {ruta}  ({len(contenido):,} caracteres)")
    print()

    bloques = extraer_jsons(contenido)
    print(f"Bloques JSON detectados: {len([b for b in bloques if b[0] is not None])}")
    errores_json = [b for b in bloques if b[0] is None]
    if errores_json:
        print(f"JSON malformados ignorados: {len(errores_json)}")
    print()

    dias_importados: set[str] = set()
    mensajes_importados = 0
    duplicados_ignorados = 0
    errores = 0

    async with async_session() as session:
        for obj, error in bloques:
            if error:
                print(f"  [ERROR JSON] {error}")
                errores += 1
                continue

            fecha_str = obj.get("date", "")
            source = obj.get("source", "Reclamos Ascensores Carballino")
            mensajes = obj.get("messages", [])

            try:
                fecha_obj = date.fromisoformat(fecha_str) if fecha_str else date.today()
            except ValueError:
                print(f"  [ERROR] Fecha inválida: {fecha_str!r} — bloque omitido")
                errores += 1
                continue

            for msg in mensajes:
                message_id = msg.get("message_id", "") or ""

                # Dedup por message_id
                if message_id and await mensaje_existe(session, message_id):
                    duplicados_ignorados += 1
                    continue

                # Parsear hora y armar timestamp UTC
                hora_str = (msg.get("time") or "00:00").strip()
                try:
                    t = datetime.strptime(hora_str, "%H:%M").time()
                except ValueError:
                    t = dtime(0, 0)

                dt_ar = datetime.combine(fecha_obj, t).replace(tzinfo=TZ_AR)
                dt_utc = dt_ar.astimezone(TZ_UTC).replace(tzinfo=None)

                registro = MensajeGrupo(
                    telefono_remitente=msg.get("phone", "") or "",
                    nombre_remitente=msg.get("sender", "") or "",
                    texto=msg.get("text", "") or "",
                    mensaje_id=message_id or None,
                    reference_message_id=msg.get("reference_message_id") or None,
                    texto_citado=msg.get("quoted_text") or None,
                    fecha=fecha_obj,
                    timestamp=dt_utc,
                    source=source,
                    raw_json=json.dumps(msg, ensure_ascii=False),
                )
                session.add(registro)
                mensajes_importados += 1
                dias_importados.add(fecha_str)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                print(f"  [ERROR DB] Fecha {fecha_str}: {e}")
                errores += 1

    print("=" * 48)
    print("IMPORTACIÓN COMPLETA")
    print(f"  Días importados:      {len(dias_importados)}")
    print(f"  Mensajes importados:  {mensajes_importados}")
    print(f"  Duplicados ignorados: {duplicados_ignorados}")
    print(f"  Errores:              {errores}")
    print("=" * 48)


if __name__ == "__main__":
    archivo = sys.argv[1] if len(sys.argv) > 1 else ARCHIVO_DEFAULT
    asyncio.run(importar(archivo))
