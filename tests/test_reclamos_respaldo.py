from agent.reclamos import (
    crear_derivacion_respaldo,
    direccion_especial,
    es_emergencia_critica,
    es_reclamo_tecnico_claro,
    extraer_direccion_libre,
    inferir_quien_abre,
)


def test_emergencia_thames_se_deriva_ademas_de_indicar_llamada():
    texto = "Soy de Thames 2331. Hay una persona atrapada en un ascensor"
    assert es_emergencia_critica(texto)
    datos = crear_derivacion_respaldo(texto)
    assert datos is not None
    assert datos["direccion"] == "Thames 2331"
    assert datos["emergencia"] is True
    assert datos["tipo"].startswith("URGENTE:")


def test_emergencia_fouiller_con_direccion_libre():
    texto = "Escribo de Felix O Fouiller 5846. Recién se quedó una persona encerrada en el ascensor"
    assert extraer_direccion_libre(texto) == "Felix O Fouiller 5846"
    assert crear_derivacion_respaldo(texto) is not None


def test_libertad_reconoce_direccion_y_encargado():
    texto = (
        "Buenos dias mi nombre es Daniel soy el encargado de Libertad 1262. "
        "El ascensor no abre la puerta del 2° piso"
    )
    datos = crear_derivacion_respaldo(texto)
    assert datos is not None
    assert datos["direccion"] == "Libertad 1262"
    assert datos["quien_abre"] == "Daniel, encargado"
    assert datos["disponibilidad_pendiente"] is False


def test_reclamo_con_direccion_sin_horario_se_deriva_igual():
    datos = crear_derivacion_respaldo("Ugarteche 3050 ascensor parado")
    assert datos is not None
    assert datos["quien_abre"] == "Disponibilidad no informada"
    assert datos["disponibilidad_pendiente"] is True


def test_torre_c_es_arribenos_montaneses():
    texto = "El ascensor 4/5 de Torre C tiene movimientos raros"
    assert direccion_especial(texto) == "Arribeños / Montañeses 3150 Torre C"
    datos = crear_derivacion_respaldo(texto)
    assert datos is not None
    assert datos["direccion"] == "Arribeños / Montañeses 3150 Torre C"


def test_mensaje_mixto_con_falla_no_se_pierde_como_administrativo():
    texto = "Estamos hace una semana sin ascensor por dos tensores y necesitamos presupuesto"
    assert es_reclamo_tecnico_claro(texto)


def test_consulta_de_estado_no_crea_reclamo_nuevo():
    texto = "Quería saber si ya pasaron por Vicente López por un tema de la bomba"
    assert not es_reclamo_tecnico_claro(texto)


def test_falla_sin_direccion_no_se_deriva_hasta_identificar_edificio():
    texto = "El ascensor está parado y hace un ruido raro"
    assert crear_derivacion_respaldo(texto) is None


def test_manija_suelta_es_reclamo_tecnico():
    texto = "Libertad 1262: se le soltó la manija al ascensor que repararon ayer"
    assert es_reclamo_tecnico_claro(texto)


def test_inferir_quien_abre_sin_nombre():
    assert inferir_quien_abre("Soy la portera de Rivadavia 2207") == "Quien escribe (portera)"
