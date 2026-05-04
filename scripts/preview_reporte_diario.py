"""
scripts/preview_reporte_diario.py
Imprime el reporte diario en consola sin enviar nada ni modificar la DB.

Uso:
    python scripts/preview_reporte_diario.py              # hoy
    python scripts/preview_reporte_diario.py 2026-05-03   # fecha específica
"""

import asyncio
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory import inicializar_db
from agent.main import generar_reporte_diario_preview


async def main():
    await inicializar_db()

    fecha = None
    if len(sys.argv) > 1:
        try:
            fecha = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Fecha inválida: {sys.argv[1]}. Usar formato YYYY-MM-DD.")
            sys.exit(1)

    reporte = await generar_reporte_diario_preview(fecha)
    print(reporte)


if __name__ == "__main__":
    asyncio.run(main())
