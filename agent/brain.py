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


_PALABRAS_RECLAMO = {
    "tecnico", "técnico",
    "reclamo tecnico", "reclamo técnico",
    "servicio tecnico", "servicio técnico",
    "ascensor", "asc",
    "no funciona", "no anda",
    "parado", "trabado",
    "falla", "fallo", "averia", "avería",
    "ruido", "ruidoso", "suena", "sonido", "se escucha", "escucha",
    "video", "audio",
    "desnivelado", "no nivela",
    "puerta", "boton", "botón",
    "piso",
    "reclamo",
}
_PALABRAS_ADMIN = {
    "administracion", "administración", "factura", "facturación", "pago", "abono",
    "contrato", "recibo", "oficina", "deuda", "transferencia",
}


async def clasificar_intencion(texto: str) -> str:
    texto_norm = texto.lower().strip()

    # Administración tiene prioridad sobre reclamo para evitar falsos positivos
    for kw in _PALABRAS_ADMIN:
        if kw in texto_norm:
            logger.info(f"clasificar_intencion keyword administracion: {texto}")
            return "administracion"

    for kw in _PALABRAS_RECLAMO:
        if kw in texto_norm:
            logger.info(f"clasificar_intencion keyword reclamo: {texto}")
            return "reclamo"

    try:
        response = await client.chat.completions.create(
            model=MODELO_CHAT,
            max_tokens=10,
            messages=[
                {"role": "system", "content": "Respondé SOLO con una palabra: reclamo, administracion o desconocido. Sin explicaciones."},
                {"role": "user", "content": texto}
            ]
        )
        resultado = response.choices[0].message.content.strip().lower()
        resultado = resultado if resultado in ["reclamo", "administracion", "desconocido"] else "desconocido"
    except Exception as e:
        logger.error(f"Error clasificando: {e}")
        resultado = "desconocido"

    logger.info(f"clasificar_intencion resultado: {resultado} para texto: {texto}")
    return resultado
