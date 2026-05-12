# agent/brain.py — Cerebro del agente: conexión con Ollama (compatible OpenAI)
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml
y genera respuestas usando la API de Ollama (formato compatible con OpenAI).
"""

import os
import yaml
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Cliente Ollama con interfaz compatible OpenAI
client = AsyncOpenAI(
    base_url="https://ollama.com/v1",
    api_key=os.getenv("OLLAMA_API_KEY"),
)


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


MODELO_CHAT = "kimi-k2.6"


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
        response = await client.chat.completions.create(
            model=MODELO_CHAT,
            max_tokens=2048,
            messages=[{"role": "system", "content": system_prompt}] + mensajes,
        )

        uso = response.usage
        logger.info(f"Respuesta generada ({uso.prompt_tokens} in / {uso.completion_tokens} out)")
        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error Ollama API: {e}")
        return obtener_mensaje_error()


_SYSTEM_CLASIFICADOR = """\
Sos un clasificador de mensajes para Ascensores Carballino, empresa argentina de mantenimiento y reparación de ascensores.

Analizá el último mensaje del cliente (y el historial si lo hay) y devolvé UNA SOLA PALABRA según la intención principal:

reclamo     → El cliente reporta un problema técnico: ascensor parado, no funciona, hace ruido, está desnivelado,
              la puerta no cierra/abre, hay alguien encerrado, pide que manden un técnico, urgencia o emergencia.

administracion → El cliente consulta sobre presupuesto, precio, contrato, factura, pago, cuota, deuda,
                 instalación de ascensor nuevo, habilitación, certificación, información comercial.

desconocido → Saludo solo ("hola", "buenos días"), mensaje confuso, el cliente pide hablar con una persona,
              o no se puede determinar la intención con certeza.

Reglas importantes:
- Si el mensaje menciona el ascensor sin indicar claramente si es falla o consulta comercial → desconocido
- Si hay historial previo que aclara la intención, usalo
- Ante la duda, preferí desconocido antes que clasificar mal
- Respondé SOLO la palabra, sin puntuación, mayúsculas ni explicación\
"""


async def clasificar_intencion(texto: str, historial: list[dict] | None = None) -> str:
    """Clasifica la intención del mensaje usando el LLM con contexto del negocio."""
    mensajes_api: list[dict] = []

    # Incluir hasta los últimos 4 turnos de historial para dar contexto
    if historial:
        for m in historial[-4:]:
            mensajes_api.append({"role": m["role"], "content": m["content"]})

    mensajes_api.append({"role": "user", "content": texto})

    try:
        response = await client.chat.completions.create(
            model=MODELO_CHAT,
            max_tokens=10,
            messages=[{"role": "system", "content": _SYSTEM_CLASIFICADOR}] + mensajes_api,
        )
        raw = response.choices[0].message.content.strip().lower().split()[0]
        resultado = raw if raw in ("reclamo", "administracion", "desconocido") else "desconocido"
    except Exception as e:
        logger.error(f"Error clasificando intención: {e}")
        resultado = "desconocido"

    logger.info(f"clasificar_intencion: '{resultado}' — texto: {texto!r}")
    return resultado
