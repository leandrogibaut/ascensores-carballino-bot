"""Detección determinista de personas en riesgo y de riesgo de fuego o eléctrico.

Casos reales que la regex anterior no detectaba (relevamiento 28/09/2026) y sus
variantes con/sin tildes, mayúsculas y género. Una emergencia crítica también
debe contar como reclamo técnico claro para que no se pierda.
"""

import pytest

from agent.reclamos import (
    crear_derivacion_respaldo,
    es_emergencia_critica,
    es_reclamo_tecnico_claro,
    es_seguimiento_reclamo,
)


EMERGENCIAS = [
    # Frases que fallaban
    "estoy encerrada en el ascensor",
    "quedó mi mamá adentro del ascensor",
    "mi hijo no puede salir del ascensor",
    "se quedó trabado con el nene adentro",
    "hay alguien atrapado entre pisos",
    # encerrado/a, atrapado/a (singular, plural, sin sujeto explícito)
    "estoy encerrado",
    "quedaron encerradas dos señoras",
    "mi vecino está atrapado",
    "ATRAPADOS EN EL ASC",
    "me quede encerrada en el asc del 4to",
    # no puede salir
    "no podemos salir del ascensor",
    "no puedo salir, la puerta no abre",
    "hace 20 min que no pueden salir",
    "está hace rato sin poder salir",
    # quedó adentro
    "quedo mi mama adentro",
    "se quedaron adentro dos chicos",
    "quedó un señor adentro de la cabina",
    "estamos adentro y no abre",
    "hay una persona adentro del ascensor",
    "mi abuela sigue adentro",
    "hay un bebé adentro!!",
    # entre pisos
    "el ascensor quedó entre pisos",
    "se clavó entre dos pisos",
    "paró en el entrepiso con gente",
    "Quedo ENTRE PISOS",
    # entre el <n> y el <n>, viejo/a, trabado/a con una persona
    "la cabina quedó trabada entre el 3 y el 4 con mi vieja",
    "se paró entre el 5 y el 6",
    "quedó entre el 3ro y 4to",
    "está entre el 2 y 3 y no se mueve",
    "mi vieja quedó trabada en el ascensor",
    "el nene está trabado en el asc, no abre",
    "quedó trabado con mis viejos, hace 10 min",
    "mi viejo sigue adentro",
    # verbos de ascensor detenido + persona en la misma frase
    "se trabó el ascensor con mis viejos",
    "se trabaron con los chicos en el 7mo",
    "se quedó parado con mi nene arriba",
    "se clavó con mi abuela",
    "se paró con mi hija, no contesta nadie",
    "se detuvo el asc con una señora",
    "está detenido con gente arriba",
    # estoy/estamos/sigo/seguimos adentro (de la cabina)
    "estoy adentro",
    "estoy adentro del ascensor",
    "seguimos adentro de la cabina",
    "sigo adentro y no abre la puerta",
    # olor a quemado, humo, chispas
    "sale olor a quemado del motor",
    "huele a quemado en la sala de máquinas",
    "hay un olorcito a quemado en el palier",
    "OLOR A QUEMADO EN LA CABINA",
    "sale humo del hueco",
    "hay una humareda en la sala de maquinas",
    "chispea la botonera",
    "chispeó el tablero y se cortó",
    "saltan chispas del tablero",
    "hizo un chispazo y se apagó",
    "está chisporroteando el cable",
    # regex previa: debe seguir funcionando igual
    "Hay una persona atrapada en un ascensor",
    "hay gente adentro",
    "sale humo del motor",
    "hubo un accidente en el ascensor",
    "riesgo eléctrico en la sala de máquinas",
    "riesgo electrico en la sala de maquinas",
]


NO_EMERGENCIAS = [
    "el ascensor está parado en el 3",
    "la puerta del 2do piso no cierra",
    "hace ruido al subir",
    "¿van a venir hoy?",
    "hola buenas tardes",
    "necesito la factura de agosto",
    "gracias, ya funciona",
    "el ascensor anda bien, solo queríamos consultar el presupuesto",
    "hay agua en el foso",
    "pisos 3 y 4 no nivelan",
    # adentro de otro lugar o cierre
    "estoy adentro del edificio esperando al técnico",
    "ya estoy adentro, gracias",
    "ya estamos adentro gracias!",
    "estamos adentro de la oficina",
    "sigo adentro del depto, avisame cuando llegue el técnico",
    "estoy adentro del hall",
    "seguimos adentro del palier esperando",
    "estoy adentro de la casa",
    # trabado/a sin persona
    "la puerta está trabada",
    "la puerta del 2do quedó trabada, la abrió el encargado",
    "el ascensor está trabado en PB",
    # verbos de ascensor detenido sin persona, o "trabaja/trabajando"
    "se paró el ascensor",
    "se clavó el ascensor en el 4to",
    "el ascensor se detuvo en PB",
    "está detenido desde ayer",
    "mi hijo trabaja en el edificio, el ascensor hace ruido",
    "el vecino dice que están trabajando en la sala de máquinas",
]


@pytest.mark.parametrize("texto", EMERGENCIAS)
def test_persona_en_riesgo_es_emergencia_critica(texto):
    assert es_emergencia_critica(texto)


@pytest.mark.parametrize("texto", EMERGENCIAS)
def test_emergencia_critica_es_reclamo_tecnico_claro(texto):
    assert es_reclamo_tecnico_claro(texto)


@pytest.mark.parametrize("texto", NO_EMERGENCIAS)
def test_falla_comun_o_consulta_no_es_emergencia_critica(texto):
    assert not es_emergencia_critica(texto)


def test_emergencia_nueva_con_direccion_se_deriva_como_urgente():
    datos = crear_derivacion_respaldo("Thames 2331, mi hijo no puede salir del ascensor")
    assert datos is not None
    assert datos["direccion"] == "Thames 2331"
    assert datos["emergencia"] is True
    assert datos["tipo"].startswith("URGENTE:")


def test_emergencia_no_se_confunde_con_seguimiento():
    # "¿van a venir?" + persona atrapada: nunca es un simple seguimiento.
    texto = "¿van a venir? sigue mi mamá adentro"
    assert es_emergencia_critica(texto)
    assert es_reclamo_tecnico_claro(texto)
    assert not (es_seguimiento_reclamo(texto) and not es_reclamo_tecnico_claro(texto))
