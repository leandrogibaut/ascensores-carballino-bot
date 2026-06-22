# tests/test_conocimiento_csv.py
import pytest
from agent.conocimiento import cargar_contactos_csv, buscar_contacto_csv

# ─── cargar_contactos_csv ───────────────────────────────────────────────────

def test_carga_csv_retorna_dict():
    resultado = cargar_contactos_csv()
    assert isinstance(resultado, dict)
    assert len(resultado) > 0

def test_carga_csv_tiene_telefonos_normalizados():
    resultado = cargar_contactos_csv()
    for tel in resultado.keys():
        assert "@" not in tel
        assert "+" not in tel
        assert " " not in tel

def test_carga_csv_contacto_algodonera_presente():
    resultado = cargar_contactos_csv()
    assert "5491139357151" in resultado
    filas = resultado["5491139357151"]
    assert len(filas) == 1
    assert filas[0]["direccion_reclamo"].strip() == "Álvarez Thomas 198"

def test_carga_csv_carina_presente():
    resultado = cargar_contactos_csv()
    assert "5491137032400" in resultado
    filas = resultado["5491137032400"]
    assert filas[0]["nombre_contacto"].strip() == "Carina"

# ─── buscar_contacto_csv ────────────────────────────────────────────────────

def test_lookup_telefono_unica_direccion():
    resultado = buscar_contacto_csv("5491139357151")
    assert resultado is not None
    assert resultado["tipo"] == "unica"
    assert resultado["fila"]["direccion_reclamo"].strip() == "Álvarez Thomas 198"

def test_lookup_telefono_multi_direccion():
    resultado = buscar_contacto_csv("8121720")
    assert resultado is not None
    assert resultado["tipo"] == "multi"
    assert len(resultado["filas"]) > 1

def test_lookup_telefono_desconocido():
    resultado = buscar_contacto_csv("5499999999999")
    assert resultado is None

def test_lookup_normaliza_sufijo_whatsapp():
    resultado = buscar_contacto_csv("5491139357151@s.whatsapp.net")
    assert resultado is not None
    assert resultado["tipo"] == "unica"

def test_lookup_normaliza_prefijo_mas():
    resultado = buscar_contacto_csv("+5491139357151")
    assert resultado is not None

def test_lookup_vacio():
    assert buscar_contacto_csv("") is None
    assert buscar_contacto_csv(None) is None


# ─── detectar_direccion_en_mensaje ──────────────────────────────────────────

from agent.conocimiento import detectar_direccion_en_mensaje

# Fixtures — simulan contactos multi-dirección

_FILAS_ALGODONERA = [
    {
        "direccion_reclamo": "Álvarez Thomas 198",
        "alias": "Algodonera, Alvarez Thomas, guardia",
        "grupo_cliente_adm": "La Algodonera",
        "sector_torre": "",
    },
    {
        "direccion_reclamo": "Santos Dumont",
        "alias": "Algodonera, Santos Dumont, guardia",
        "grupo_cliente_adm": "La Algodonera",
        "sector_torre": "",
    },
    {
        "direccion_reclamo": "Concepción Arenal 3425",
        "alias": "Algodonera, Concepcion Arenal, guardia",
        "grupo_cliente_adm": "La Algodonera",
        "sector_torre": "",
    },
]

_FILAS_MULTI_GENERICO = [
    {
        "direccion_reclamo": "Castro 1137",
        "alias": "CASTRO, CASTRO 1137",
        "grupo_cliente_adm": "CASTRO 1137",
        "sector_torre": "",
    },
    {
        "direccion_reclamo": "Santa Fe 2306",
        "alias": "SANTA FE, SANTA FE 2306",
        "grupo_cliente_adm": "SANTA FE 2306",
        "sector_torre": "",
    },
]

_FILAS_BALBIN = [
    {
        "direccion_reclamo": "Balbín 2421",
        "alias": "Balbin 2421",
        "grupo_cliente_adm": "Edificio Norte",
        "sector_torre": "",
    },
    {
        "direccion_reclamo": "Balbín 2640",
        "alias": "Balbin 2640",
        "grupo_cliente_adm": "Edificio Norte",
        "sector_torre": "",
    },
]

# — Caso 1: Balbín 2421 detecta la dirección correcta —

def test_balbin_2421_detecta_direccion_correcta():
    msg = "Hola, tengo un reclamo en Balbín 2421, el ascensor no funciona."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_BALBIN)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Balbín 2421"

def test_balbin_sin_tilde_detecta_igual():
    msg = "Hola, el ascensor de Balbin 2421 está parado."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_BALBIN)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Balbín 2421"

def test_balbin_2640_no_confunde_con_2421():
    msg = "Reclamo en Balbín 2640."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_BALBIN)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Balbín 2640"

# — Caso 2: "La Algodonera" solo no alcanza si hay varias direcciones —

def test_solo_grupo_adm_no_elige_direccion():
    msg = "Hola, hay un reclamo en La Algodonera."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is None

def test_solo_grupo_adm_generico_no_elige_direccion():
    msg = "Tengo un problema en el edificio."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is None

# — Caso 3: Alias detecta dirección —

def test_alias_detecta_direccion():
    msg = "Hola, tengo un problema en Santos Dumont."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Santos Dumont"

def test_alias_detecta_ignorando_tildes():
    msg = "El ascensor de Concepcion Arenal está parado."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is not None
    assert "Arenal" in resultado["direccion_reclamo"]

def test_alias_detecta_ignorando_mayusculas():
    msg = "reclamo en castro 1137"
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_MULTI_GENERICO)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Castro 1137"

# — Caso 4: Mensaje sin dirección devuelve None —

def test_mensaje_sin_direccion_retorna_none():
    msg = "Hola, tengo un reclamo."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is None

def test_mensaje_solo_saludo_retorna_none():
    msg = "Buenas tardes."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_MULTI_GENERICO)
    assert resultado is None

def test_mensaje_vacio_retorna_none():
    assert detectar_direccion_en_mensaje("", _FILAS_ALGODONERA) is None

def test_lista_vacia_retorna_none():
    assert detectar_direccion_en_mensaje("hola Castro 1137", []) is None

# — Casos adicionales —

def test_detecta_por_direccion_reclamo_directa():
    msg = "Hola, tengo un reclamo en Álvarez Thomas 198, ascensor parado."
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_ALGODONERA)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Álvarez Thomas 198"

def test_primera_fila_gana_cuando_ambas_en_mensaje():
    msg = "Santa Fe y Castro tienen problemas"
    resultado = detectar_direccion_en_mensaje(msg, _FILAS_MULTI_GENERICO)
    assert resultado is not None
    assert resultado["direccion_reclamo"] == "Castro 1137"


# ─── construir_contexto_desde_csv ───────────────────────────────────────────

from datetime import datetime
from agent.conocimiento import construir_contexto_desde_csv, construir_contexto_multi_sin_match

# Fixtures de contexto

_FILA_ALGODONERA_ALVAREZ = {
    "nombre_contacto": "Guardia Álvarez Thomas 198",
    "tipo_contacto": "guardia",
    "grupo_cliente_adm": "La Algodonera",
    "direccion_reclamo": "Álvarez Thomas 198",
    "sector_torre": "",
    "quien_abre": "Guardia / personal del edificio",
    "horario": "8 a 19 hs",
    "guardia_24hs": "False",
    "preguntar_fuera_de_horario": "True",
    "observaciones": "Si escribe después de las 19 hs, preguntar si hay alguien.",
    "equipos": "ascensores / bombas",
    "alias": "Algodonera, Alvarez Thomas, guardia",
}

_FILA_GUARDIA_24 = {
    "nombre_contacto": "Guardia Santos Dumont",
    "tipo_contacto": "guardia",
    "grupo_cliente_adm": "La Algodonera",
    "direccion_reclamo": "Santos Dumont",
    "sector_torre": "",
    "quien_abre": "Guardia",
    "horario": "24 hs",
    "guardia_24hs": "True",
    "preguntar_fuera_de_horario": "False",
    "observaciones": "",
    "equipos": "ascensores / bombas",
    "alias": "Algodonera, Santos Dumont, guardia",
}

_FILA_CARINA = {
    "nombre_contacto": "Carina",
    "tipo_contacto": "intendenta",
    "grupo_cliente_adm": "Arribeños / Montañeses 3150 Torre C",
    "direccion_reclamo": "Montañeses 3150",
    "sector_torre": "Torre C",
    "quien_abre": "Carina, intendenta",
    "horario": "24 hs",
    "guardia_24hs": "True",
    "preguntar_fuera_de_horario": "False",
    "observaciones": "Carina es la intendenta.",
    "equipos": "ascensores / bombas",
    "alias": "Carina, Montañeses, Arribeños, Torre C",
}

_FILA_HORARIO_PARCIAL = {
    "nombre_contacto": "MIRTA",
    "tipo_contacto": "encargado/guardia",
    "grupo_cliente_adm": "ACEVEDO 373",
    "direccion_reclamo": "ACEVEDO 373",
    "sector_torre": "",
    "quien_abre": "MIRTA",
    "horario": "8-12HS",
    "guardia_24hs": "False",
    "preguntar_fuera_de_horario": "False",
    "observaciones": "",
    "equipos": "ascensores",
    "alias": "ACEVEDO, ACEVEDO 373",
}

# — 1. Dirección única Algodonera / Álvarez Thomas —
# grupo_cliente_adm distinto de direccion_reclamo → ambos deben aparecer

def test_contexto_incluye_seccion_cliente_registrado():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "CLIENTE REGISTRADO" in ctx

def test_contexto_muestra_grupo_adm_cuando_difiere():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "La Algodonera" in ctx

def test_contexto_muestra_direccion_reclamo_alvarez():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "Álvarez Thomas 198" in ctx

def test_contexto_no_usa_grupo_adm_como_direccion_de_despacho():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    # La dirección de reclamo operativa debe ser Álvarez Thomas, no La Algodonera
    assert "Dirección de reclamo: Álvarez Thomas 198" in ctx

def test_contexto_no_pedir_direccion():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "NO pedir dirección" in ctx

def test_contexto_tag_solicitud_incluye_grupo_y_direccion():
    # El tag [SOLICITUD_COMPLETA] debe incluir "La Algodonera — Álvarez Thomas 198"
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "La Algodonera — Álvarez Thomas 198" in ctx

# — 2. guardia_24hs=True → no pedir quién abre ni horario —

def test_contexto_guardia_24_no_pedir_quien_abre():
    ctx = construir_contexto_desde_csv(_FILA_GUARDIA_24)
    assert "NO pedir quién abre" in ctx

def test_contexto_guardia_24_no_regla_especial():
    # Con guardia 24hs no debe aparecer la regla de fuera de horario
    ctx = construir_contexto_desde_csv(_FILA_GUARDIA_24)
    assert "REGLA ESPECIAL" not in ctx

# — 3. Fuera de horario → preguntar disponibilidad —

def test_contexto_fuera_de_horario_activa_regla_especial():
    hora_fuera = datetime(2026, 6, 22, 21, 0)  # 21:00, fuera de 8-19
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ, hora_actual=hora_fuera)
    assert "REGLA ESPECIAL" in ctx

def test_contexto_dentro_de_horario_no_regla_especial():
    hora_dentro = datetime(2026, 6, 22, 10, 0)  # 10:00, dentro de 8-19
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ, hora_actual=hora_dentro)
    assert "REGLA ESPECIAL" not in ctx

# — 4. sector_torre se append a direccion_reclamo —

def test_contexto_sector_torre_appendeado():
    ctx = construir_contexto_desde_csv(_FILA_CARINA)
    assert "Montañeses 3150 Torre C" in ctx

def test_contexto_tag_carina_incluye_grupo_y_torre():
    ctx = construir_contexto_desde_csv(_FILA_CARINA)
    assert "Arribeños / Montañeses 3150 Torre C — Montañeses 3150 Torre C" in ctx

# — 5. grupo_cliente_adm == direccion_reclamo → no duplicar —

def test_contexto_grupo_igual_direccion_no_duplica():
    ctx = construir_contexto_desde_csv(_FILA_HORARIO_PARCIAL)
    # "ACEVEDO 373" aparece como dirección; no debe aparecer "ACEVEDO 373 — ACEVEDO 373"
    assert "ACEVEDO 373 — ACEVEDO 373" not in ctx

# — 6. Equipos y observaciones se incluyen —

def test_contexto_incluye_equipos():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "ascensores / bombas" in ctx

def test_contexto_incluye_observaciones():
    ctx = construir_contexto_desde_csv(_FILA_ALGODONERA_ALVAREZ)
    assert "19 hs" in ctx  # parte de la observación sobre fuera de horario

# ─── construir_contexto_multi_sin_match ─────────────────────────────────────

# — Multi-dirección sin match: pedir solo dirección —

def test_multi_sin_match_pide_de_que_direccion():
    filas = [_FILA_ALGODONERA_ALVAREZ, _FILA_GUARDIA_24]
    ctx = construir_contexto_multi_sin_match(filas)
    assert "De qué dirección" in ctx or "de qué dirección" in ctx

def test_multi_sin_match_no_adivina():
    filas = [_FILA_ALGODONERA_ALVAREZ, _FILA_GUARDIA_24]
    ctx = construir_contexto_multi_sin_match(filas)
    assert "NO adivines" in ctx

def test_multi_sin_match_lista_direcciones_alvarez_y_santos():
    filas = [_FILA_ALGODONERA_ALVAREZ, _FILA_GUARDIA_24]
    ctx = construir_contexto_multi_sin_match(filas)
    assert "Álvarez Thomas 198" in ctx
    assert "Santos Dumont" in ctx

def test_multi_sin_match_incluye_torre_en_lista():
    filas = [_FILA_ALGODONERA_ALVAREZ, _FILA_CARINA]
    ctx = construir_contexto_multi_sin_match(filas)
    assert "Montañeses 3150 Torre C" in ctx

# — Multi-dirección con match: tratar como dirección única —

def test_multi_con_match_usa_fila_detectada():
    # Simular que detectar_direccion_en_mensaje ya devolvió _FILA_GUARDIA_24
    # y ahora se llama construir_contexto_desde_csv con esa fila
    ctx = construir_contexto_desde_csv(_FILA_GUARDIA_24)
    assert "Santos Dumont" in ctx
    assert "CLIENTE REGISTRADO" in ctx
    assert "NO pedir dirección" in ctx

def test_multi_con_match_no_pide_direccion():
    ctx = construir_contexto_desde_csv(_FILA_GUARDIA_24)
    # No debe pedir dirección ya que la fila fue detectada
    assert "NO pedir dirección" in ctx
