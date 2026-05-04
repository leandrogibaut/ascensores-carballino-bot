# agent/conocimiento.py — Normalización silenciosa de direcciones conocidas

import os
import re
import unicodedata
import yaml
import logging

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
