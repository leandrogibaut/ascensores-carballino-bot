# agent/brain.py — Cerebro del agente: conexión con LLM (Ollama o Gemini)

import os
import yaml
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "kimi-k2.6")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_ollama_client = AsyncOpenAI(
    base_url="https://ollama.com/v1",
    api_key=os.getenv("OLLAMA_API_KEY"),
)

_gemini_client: AsyncOpenAI | None = None
if os.getenv("GEMINI_API_KEY"):
    _gemini_client = AsyncOpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY"),
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
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intente nuevamente en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpe, no pude interpretar su mensaje. ¿Podría reformularlo, por favor?")


async def _llamar_llm(client: AsyncOpenAI, model: str, system_prompt: str, mensajes: list) -> str:
    response = await client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "system", "content": system_prompt}] + mensajes,
    )
    uso = response.usage
    logger.info(f"Respuesta generada ({uso.prompt_tokens} in / {uso.completion_tokens} out)")
    return response.choices[0].message.content


async def generar_respuesta(mensaje: str, historial: list[dict], contexto_cliente: str = "") -> str:
    """
    Genera una respuesta usando el LLM configurado (Gemini o Ollama).
    Si LLM_PROVIDER=gemini y Gemini falla, usa Ollama como fallback.
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()
    if contexto_cliente:
        system_prompt = system_prompt + "\n\n" + contexto_cliente
    historial_reciente = historial[-15:] if len(historial) > 15 else historial

    mensajes = [{"role": msg["role"], "content": msg["content"]} for msg in historial_reciente]
    mensajes.append({"role": "user", "content": mensaje})

    try:
        if LLM_PROVIDER == "gemini" and _gemini_client:
            try:
                respuesta = await _llamar_llm(_gemini_client, GEMINI_MODEL, system_prompt, mensajes)
                logger.info(f"LLM usado: gemini / {GEMINI_MODEL}")
                return respuesta
            except Exception as e:
                logger.warning(f"Gemini falló ({e}), usando Ollama como fallback")

        respuesta = await _llamar_llm(_ollama_client, OLLAMA_MODEL, system_prompt, mensajes)
        logger.info(f"LLM usado: ollama / {OLLAMA_MODEL}")
        return respuesta

    except Exception as e:
        logger.error(f"Error LLM: {e}")
        return obtener_mensaje_error()
