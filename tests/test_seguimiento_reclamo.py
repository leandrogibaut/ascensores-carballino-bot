from agent.reclamos import es_seguimiento_reclamo


def test_reconoce_todas_las_frases_de_seguimiento_pedidas():
    frases = [
        "¿van a venir?",
        "¿me confirmás si van a venir?",
        "¿vieron los mensajes de ayer?",
        "¿vieron el reclamo?",
        "¿hay novedades?",
        "¿alguna novedad?",
        "¿pasaron el reclamo?",
        "¿ya lo pasaron?",
        "¿lo pudieron ver?",
        "¿vinieron?",
        "¿cuándo vienen?",
    ]
    for frase in frases:
        assert es_seguimiento_reclamo(frase), f"no reconoció como seguimiento: {frase}"


def test_me_confirmas_si_van_a_venir_es_seguimiento():
    assert es_seguimiento_reclamo("Buenas me confirmas si van a venir??")


def test_vieron_los_mensajes_de_ayer_es_seguimiento():
    assert es_seguimiento_reclamo("No vieron los msjs de ayer??")


def test_hay_novedades_es_seguimiento():
    assert es_seguimiento_reclamo("¿Hay novedades?")


def test_pasaron_el_reclamo_es_seguimiento():
    assert es_seguimiento_reclamo("¿Pasaron el reclamo?")


def test_ascensor_quedo_parado_no_es_seguimiento():
    assert not es_seguimiento_reclamo("El ascensor 3 quedó parado")


def test_bomba_no_arranca_no_es_seguimiento():
    assert not es_seguimiento_reclamo("La bomba no arranca")


def test_van_a_venir_tecnicos_con_falla_nueva_no_es_solo_seguimiento():
    """Evita el falso positivo: si el mensaje describe una falla nueva clara junto
    con 'van a venir', no debe tratarse como un simple seguimiento sin falla."""
    assert not es_seguimiento_reclamo(
        "Van a venir técnicos por el ascensor 3 que quedó parado"
    )


def test_saludo_simple_no_es_seguimiento():
    assert not es_seguimiento_reclamo("Hola buen día")


def test_reclamo_nuevo_claro_sigue_detectandose_como_reclamo_no_seguimiento():
    from agent.reclamos import es_reclamo_tecnico_claro

    texto = "El ascensor de Ugarteche 3050 no funciona, quedó parado en PB"
    assert es_reclamo_tecnico_claro(texto)
    assert not es_seguimiento_reclamo(texto)
