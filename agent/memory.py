# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit

"""
Sistema de memoria del agente. Guarda el historial de conversaciones
por número de teléfono usando SQLite (local) o PostgreSQL (producción).
"""

import os
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Date
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# Configuración de base de datos
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

# Si es PostgreSQL en producción, ajustar el esquema de URL
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Modelo de mensaje en la base de datos."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" o "assistant"
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Solicitud(Base):
    """Solicitud de servicio registrada por Olivia."""
    __tablename__ = "solicitudes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono_cliente: Mapped[str] = mapped_column(String(50), index=True)
    tipo: Mapped[str] = mapped_column(String(50), default="")
    nombre: Mapped[str] = mapped_column(String(100), default="")
    consorcio: Mapped[str] = mapped_column(String(200), default="")
    direccion: Mapped[str] = mapped_column(String(200), default="")
    quien_abre: Mapped[str] = mapped_column(String(100), default="")
    piso_depto: Mapped[str] = mapped_column(String(50), default="")
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | resuelto | pendiente_con_nota
    notas_tecnico: Mapped[str] = mapped_column(Text, default="")
    fecha: Mapped[date] = mapped_column(Date, default=date.today)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        session.add(mensaje)
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """
    Recupera los últimos N mensajes de una conversación.

    Args:
        telefono: Número de teléfono del cliente
        limite: Máximo de mensajes a recuperar (default: 20)

    Returns:
        Lista de diccionarios con role y content
    """
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()

        # Invertir para orden cronológico (los más recientes están primero)
        mensajes.reverse()

        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def guardar_solicitud(datos: dict) -> int:
    """Guarda una nueva solicitud de servicio. Retorna el ID asignado."""
    async with async_session() as session:
        solicitud = Solicitud(
            telefono_cliente=datos.get("telefono_cliente", ""),
            tipo=datos.get("tipo", ""),
            nombre=datos.get("nombre", ""),
            consorcio=datos.get("consorcio", ""),
            direccion=datos.get("direccion", ""),
            quien_abre=datos.get("quien_abre", ""),
            piso_depto=datos.get("piso_depto", ""),
            estado="pendiente",
            fecha=date.today(),
            timestamp=datetime.utcnow(),
        )
        session.add(solicitud)
        await session.commit()
        await session.refresh(solicitud)
        return solicitud.id


async def obtener_solicitudes_del_dia() -> list[Solicitud]:
    """Retorna todas las solicitudes del día actual."""
    async with async_session() as session:
        query = (
            select(Solicitud)
            .where(Solicitud.fecha == date.today())
            .order_by(Solicitud.timestamp.asc())
        )
        result = await session.execute(query)
        return result.scalars().all()


async def actualizar_estado_solicitud(solicitud_id: int, estado: str, notas: str = ""):
    """Actualiza el estado de una solicitud."""
    async with async_session() as session:
        query = select(Solicitud).where(Solicitud.id == solicitud_id)
        result = await session.execute(query)
        solicitud = result.scalar_one_or_none()
        if solicitud:
            solicitud.estado = estado
            solicitud.notas_tecnico = notas
            await session.commit()


async def obtener_solicitud_activa_por_telefono(telefono: str) -> "Solicitud | None":
    """Retorna la solicitud registrada hoy para este teléfono, si ya existe."""
    async with async_session() as session:
        query = (
            select(Solicitud)
            .where(
                (Solicitud.telefono_cliente == telefono) &
                (Solicitud.fecha == date.today())
            )
            .order_by(Solicitud.timestamp.desc())
            .limit(1)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def buscar_solicitud_por_id(solicitud_id: int) -> "Solicitud | None":
    """Busca una solicitud por su ID."""
    async with async_session() as session:
        query = select(Solicitud).where(Solicitud.id == solicitud_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def buscar_solicitud_por_direccion(texto: str) -> Solicitud | None:
    """Busca la solicitud pendiente del día cuya dirección o consorcio aparece en el texto."""
    solicitudes = await obtener_solicitudes_del_dia()
    texto_lower = texto.lower()
    for s in solicitudes:
        if s.estado == "pendiente" or s.estado == "pendiente_con_nota":
            if s.direccion and s.direccion.lower() in texto_lower:
                return s
            if s.consorcio and s.consorcio.lower() in texto_lower:
                return s
    return None


async def tiene_mensajes_recientes(telefono: str, horas: int = 4) -> bool:
    """Retorna True si hay mensajes del teléfono en las últimas N horas."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=horas)
    async with async_session() as session:
        query = select(Mensaje).where(
            (Mensaje.telefono == telefono) & (Mensaje.timestamp >= cutoff)
        ).limit(1)
        result = await session.execute(query)
        return result.scalar_one_or_none() is not None


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()
