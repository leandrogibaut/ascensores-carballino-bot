"""Reglas de seguridad para destinos grupales de WhatsApp."""

import os


def _normalizar_id_grupo(valor: str | None) -> str:
    return (
        str(valor or "")
        .strip()
        .lower()
        .replace("@g.us", "")
        .replace("-group", "")
    )


def es_destino_grupo(destino: str | None, grupo_configurado: str | None = None) -> bool:
    """Detecta grupos aun cuando Z-API y Whapi usan formatos diferentes."""
    valor = str(destino or "").strip().lower()
    if not valor:
        return False
    if valor.endswith("@g.us") or valor.endswith("-group"):
        return True

    configurado = (
        grupo_configurado
        if grupo_configurado is not None
        else os.getenv("WHAPI_GROUP_ID", "")
    )
    grupo_normalizado = _normalizar_id_grupo(configurado)
    return bool(grupo_normalizado and _normalizar_id_grupo(valor) == grupo_normalizado)
