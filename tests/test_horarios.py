from datetime import datetime
from zoneinfo import ZoneInfo

from agent.horarios import estado_atencion_olivia, olivia_debe_atender


TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")


def momento(anio: int, mes: int, dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(anio, mes, dia, hora, minuto, tzinfo=TZ_AR)


def test_lunes_a_viernes_antes_de_las_8_atiende():
    assert olivia_debe_atender(momento(2026, 8, 4, 7, 59))


def test_lunes_a_viernes_desde_las_8_no_atiende():
    estado = estado_atencion_olivia(momento(2026, 8, 4, 8, 0))
    assert estado["respuestas_automaticas"] is False
    assert estado["motivo"] == "horario_de_atencion_humana"


def test_lunes_a_viernes_hasta_las_18_no_atiende():
    assert not olivia_debe_atender(momento(2026, 8, 4, 17, 59))
    assert olivia_debe_atender(momento(2026, 8, 4, 18, 0))


def test_fin_de_semana_atiende_todo_el_dia():
    assert olivia_debe_atender(momento(2026, 8, 8, 10, 0))
    assert olivia_debe_atender(momento(2026, 8, 9, 15, 0))


def test_feriado_nacional_atiende_aunque_sea_horario_de_oficina():
    estado = estado_atencion_olivia(momento(2026, 8, 17, 10, 0))
    assert estado["respuestas_automaticas"] is True
    assert str(estado["motivo"]).startswith("feriado:")


def test_dia_habil_normal_no_se_confunde_con_feriado():
    estado = estado_atencion_olivia(momento(2026, 8, 4, 10, 0))
    assert estado["respuestas_automaticas"] is False
