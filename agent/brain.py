# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml
y genera respuestas usando la API de Anthropic Claude.
"""

import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Cliente de Anthropic
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asistente útil. Responde en español.")


def obtener_mensaje_error() -> str:
    """Retorna el mensaje de error configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intente nuevamente en unos minutos.")


def obtener_mensaje_fallback() -> str:
    """Retorna el mensaje de fallback configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpe, no pude interpretar su mensaje. ¿Podría reformularlo, por favor?")


# Modelo principal para el chat (rápido y económico)
MODELO_CHAT = "claude-haiku-4-5-20251001"


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por Claude
    """
    # Si el mensaje es muy corto o vacío, usar fallback
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Limitar historial a los últimos 15 mensajes para reducir tokens
    historial_reciente = historial[-15:] if len(historial) > 15 else historial

    # Construir mensajes para la API
    mensajes = []
    for msg in historial_reciente:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Agregar el mensaje actual
    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    try:
        response = await client.messages.create(
            model=MODELO_CHAT,
            max_tokens=1024,
            # Prompt caching: el system prompt se cachea entre llamadas,
            # reduciendo el costo de tokens de entrada repetidos
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }],
            messages=mensajes
        )

        uso = response.usage
        cache_hit = getattr(uso, "cache_read_input_tokens", 0)
        cache_miss = getattr(uso, "cache_creation_input_tokens", 0)
        logger.info(
            f"Respuesta generada ({uso.input_tokens} in / {uso.output_tokens} out"
            + (f" / {cache_hit} cache_hit / {cache_miss} cache_write)" if cache_hit or cache_miss else ")")
        )
        return response.content[0].text

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
