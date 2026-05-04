import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.conocimiento import buscar_cliente_por_texto

casos = [
    ("Rivadavia 2209. Ascensor parado. Juan hasta las 12.", "Rivadavia 2209"),
    ("el ascensor del Hotel Avenida no funciona", "Hotel Avenida 623"),
    ("Algodonera tiene el ascensor trabado", "Algodonera Dumont 3454"),
    ("Godoy Cruz 3226 sin luz en cabina", "Godoy Cruz 3226"),
    ("Godoy Cruz sin mas datos", None),
    ("una direccion que no existe 9999", None),
    ("aguero 1131 puerta rota", "Agüero 1131"),
    ("AGÜERO 1131 puerta rota", "Agüero 1131"),
    ("Banigo ascensor parado", "Av. De Mayo 935"),
    ("Hostal Cramer no sube al piso 3", "Av. Cramer 2966"),
]

ok = err = 0
for texto, esperado in casos:
    r = buscar_cliente_por_texto(texto)
    got = r["direccion"] if r else None
    estado = "OK" if got == esperado else "FAIL"
    if estado == "OK":
        ok += 1
    else:
        err += 1
    print(f"  [{estado}] esperado={esperado!r:30} got={got!r}  << {texto[:50]!r}")

print(f"\n{ok} OK / {err} FAIL")
