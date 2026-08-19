# Olivia — Asistente WhatsApp de Ascensores Carballino

> CLAUDE.md orientado al mantenimiento del proyecto ya construido.
> Para el historial de construcción ver CLAUDE.backup.md.

---

## 1. Identidad del proyecto

**Olivia** es la asistente virtual de Ascensores Carballino (I.S.V. SRL), atendiendo clientes
por WhatsApp. Clasifica mensajes, toma reclamos técnicos y deriva consultas administrativas.

- Habla SIEMPRE en español, con tono cordial, profesional y natural argentino
- Respuestas breves y claras, sin sonar robótica
- Nunca se presenta como IA, bot ni sistema automatizado
- Saludo inicial: `"Hola, soy Olivia de Ascensores Carballino. Contame brevemente en qué podemos ayudarte."`

---

## 2. Reglas críticas — NO MODIFICAR SIN REVISIÓN

### Silencio humano
- Si detecta `fromMe: true` en el webhook → activar silencio **6 horas** para esa conversación
- El silencio se chequea en **DOS momentos**: al recibir el mensaje Y justo antes de enviar respuesta
- Implementado en: `es_intervencion_humana()` (zapi.py) → `silenciar_conversacion()` → `conversacion_silenciada()` (main.py) + chequeo dentro de `_enviar_registrando()`
- Mientras está silenciada, Olivia no responde bajo ninguna circunstancia
- `fromMe: true` (y los comandos manuales del admin) son los **únicos** disparadores de
  silencio. **NO hay silencio automático de 6hs después de confirmar/derivar un reclamo**
  al grupo — se evaluó reintroducirlo y se descartó a propósito, porque dejaría a Olivia
  muda ante seguimientos legítimos del cliente ("¿van a venir?") durante esas 6hs.

### Aviso de emergencia obligatorio
Toda respuesta del LLM debe incluir (o el código agrega con `asegurar_aviso_emergencia()`):
> "Ante cualquier problema o emergencia, comuníquese directamente por llamada telefónica
> común al 4301-3967 o al 1565024510. No por llamada de WhatsApp."

La función chequea si alguno de los dos números ya está en el texto antes de agregar.

### Sin menú
No existe menú de opciones. Olivia interpreta la intención directamente con el LLM.
**Prohibido reintroducir menú** salvo pedido explícito del usuario.

### Salida a grupos de WhatsApp
- `enviar_mensaje()` (en cada proveedor) está **bloqueado para cualquier destino grupal**,
  siempre — es la defensa en profundidad heredada del incidente que originó este bloqueo.
- La **única excepción permitida** es la ruta dedicada y auditada:
  `notificar_grupo_solicitud()` (tools.py) → `enviar_mensaje_grupo()` (capa de proveedores).
  Ese método no recibe un destino arbitrario: siempre apunta al `WHAPI_GROUP_ID` configurado.
- Olivia **nunca conversa dentro del grupo** ni responde a mensajes que los técnicos
  escriben ahí — esos mensajes solo se leen para actualizar el estado de una solicitud
  (`analizar_mensaje_tecnico()` en main.py).
- **Prohibido** agregar un segundo camino de salida al grupo o hacer que `enviar_mensaje()`
  deje de bloquear destinos grupales sin revisión explícita.

### Tono
Cordial, profesional, argentino y breve. Tuteo o usted según el contexto, siempre natural.

---

## 3. Stack técnico

| Componente       | Tecnología                          |
|-----------------|-------------------------------------|
| Runtime          | Python 3.11+                        |
| Servidor         | FastAPI + Uvicorn                   |
| IA               | Ollama — modelo `kimi-k2.6`         |
| WhatsApp         | Z-API                               |
| Transcripción    | Groq Whisper (`whisper-large-v3-turbo`) |
| Base de datos    | PostgreSQL (Railway) / SQLite (local) |
| Deploy           | Railway                             |
| Variables        | python-dotenv                       |

---

## 4. Arquitectura

```
whatsapp-agentkit/
├── agent/
│   ├── main.py          ← FastAPI + webhook handler + debounce + silencio
│   ├── brain.py         ← Ollama API + system prompt desde prompts.yaml
│   ├── memory.py        ← SQLAlchemy: mensajes, solicitudes, intervenciones, grupo
│   ├── reclamos.py      ← Red de seguridad determinista para no perder reclamos + detección de seguimientos
│   ├── tools.py         ← notificar_grupo_solicitud() (ruta dedicada al grupo), es_emergencia(), etc.
│   ├── reports.py       ← Reporte diario de solicitudes
│   ├── conocimiento.py  ← Lookup de clientes en clientes_direcciones.yaml
│   └── providers/
│       ├── base.py      ← Clase abstracta ProveedorWhatsApp + MensajeEntrante
│       ├── __init__.py  ← Factory: obtener_proveedor()
│       └── zapi.py      ← Adaptador Z-API (provider activo en producción)
├── config/
│   ├── business.yaml          ← Datos del negocio
│   ├── prompts.yaml           ← System prompt de Olivia
│   └── clientes_direcciones.yaml ← Base de clientes para normalización
├── tests/
│   └── test_local.py    ← Chat interactivo en terminal (sin WhatsApp)
├── .env                 ← API keys (NUNCA a GitHub)
└── requirements.txt
```

### Flujo de un mensaje

```
Cliente escribe en WhatsApp
    ↓
Z-API → POST /webhook (main.py)
    ↓
Early return: ¿fromMe=true? → silenciar_conversacion() y salir
    ↓
¿Grupo interno? → guardar_mensaje_grupo() y analizar reply técnico
    ↓
¿Conversación silenciada? → ignorar
    ↓
Debounce: acumular mensajes 10s (activa) / 120s (nueva)
    ↓
procesar_acumulados() → procesar_mensaje_cliente()
    ↓
obtener_historial() → ¿es seguimiento de un reclamo pendiente reciente (≤24hs)?
    → inyecta contexto de esa solicitud (solo lectura, no crea ni modifica nada)
    ↓
generar_respuesta() (Ollama kimi-k2.6)
    ↓
¿[SOLICITUD_COMPLETA:] o respaldo con dirección+falla?
    ¿hay solicitud activa del mismo teléfono (<10 min) con misma dirección o sin dirección nueva?
        → SÍ: agregar_ampliacion_solicitud() + notificar_grupo_solicitud() (actualización, mismo ID)
        → NO: guardar_solicitud() + notificar_grupo_solicitud() (solicitud nueva)
    → "Perfecto, el reclamo quedó registrado." SOLO si notificar_grupo_solicitud() tuvo éxito
    → si falla, la solicitud queda guardada igual y se responde el fallback (no se afirma
      que fue avisado el equipo técnico)
¿[DERIVAR_ADMIN]?       → marcar_estado_conversacion("pendiente_admin")
    ↓
asegurar_aviso_emergencia()
    ↓
¿conversacion_silenciada()? → cancelar envío
    ↓
_enviar_registrando() → Z-API envía respuesta al cliente
```

---

## 5. Flujo de negocio — Reclamos

### Tags que Olivia emite (invisibles al cliente)

**`[SOLICITUD_COMPLETA: {dirección}. {falla}. {quién abre/horario}.]`**
- Se emite con dirección + falla; quién abre/horario es opcional y se informa como
  `Disponibilidad no informada` cuando falta
- `main.py` lo intercepta y decide entre dos caminos según si hay una solicitud reciente
  del mismo teléfono (ver "Ampliaciones vs. seguimientos" abajo):
  - **Reclamo nuevo**: `guardar_solicitud()` en Postgres → `notificar_grupo_solicitud()`
    (ruta dedicada, ver "Salida a grupos" en la sección 2).
  - **Ampliación** de una solicitud existente (<10 min, misma dirección o sin dirección
    nueva): `agregar_ampliacion_solicitud()` anexa el texto a la solicitud existente
    (mismo `solicitud_id`, nunca crea una segunda) → `notificar_grupo_solicitud()` con una
    única "Actualización #N" si la solicitud original ya había llegado al grupo.
- El cliente ve **"Perfecto, el reclamo quedó registrado."** únicamente si
  `notificar_grupo_solicitud()` tuvo éxito. Si falla, la solicitud igual queda guardada en
  Postgres, se loguea `ERROR ENVÍO GRUPO`, y el cliente recibe `MENSAJE_RECLAMO_SIN_GRUPO`
  (main.py) — nunca se afirma que el equipo técnico ya fue avisado.
- `reclamos.py` deriva como respaldo si el modelo omite el tag pese a existir una falla y
  una dirección identificable
- Una emergencia crítica conserva la respuesta de llamada telefónica y también intenta
  registrar y notificar el caso cuando se conoce la dirección

**`[DERIVAR_ADMIN]`**
- Se emite para consultas administrativas (facturas, pagos, contratos)
- `main.py` marca la conversación como `pendiente_admin` y deja de responder
- El equipo humano toma la conversación

### Ampliaciones vs. seguimientos — dos mecanismos distintos, no confundir

| | Ampliación | Seguimiento |
|---|---|---|
| Ventana | **10 minutos** (`obtener_solicitud_activa_por_telefono`) | **24 horas** (`obtener_solicitud_pendiente_reciente_por_telefono`) |
| Dispara cuando | el modelo emite `[SOLICITUD_COMPLETA]`/respaldo con misma dirección o sin dirección propia | el mensaje matchea `es_seguimiento_reclamo()` ("¿van a venir?", "¿hay novedades?", "¿vieron los mensajes?", etc.) |
| Efecto | anexa texto a la solicitud existente (`agregar_ampliacion_solicitud`), notifica **una** actualización al grupo con el mismo `solicitud_id` | inyecta contexto de solo lectura al LLM (solicitud_id, dirección, falla, estado, fecha) — **no crea ni modifica nada** |
| Dirección distinta | se trata como **reclamo nuevo independiente**, no se fusiona (protege a contactos multi-dirección) | no aplica — el seguimiento no toca la dirección |
| Reglas al responder | igual que un reclamo nuevo (depende de que la notificación al grupo tenga éxito) | no repetir dirección/falla, no crear tag nuevo, no inventar técnico/horario, reconocer solo el estado real |

### Grupo interno de técnicos
- Olivia solo puede escribirle al grupo a través de `notificar_grupo_solicitud()` (ver
  "Salida a grupos" en la sección 2) — nunca conversa ni responde ahí.
- Los técnicos responden con reply → se vincula por `referenceMessageId` → `#N` → dirección (3 prioridades)
- Sus respuestas actualizan el estado de la solicitud: `pendiente` → `resuelto` / `pendiente_con_nota`

### Reporte diario
- Todos los días a las **20:00hs** el scheduler envía un JSON con los mensajes del grupo al número configurado en `REPORTE_DIARIO_TELEFONO`

---

## 6. Reglas de trabajo — antes de modificar código

1. **Leer** el archivo completo (o la sección relevante) antes de tocar nada
2. **Explicar** brevemente qué va a cambiar y por qué
3. **Cambios mínimos** — solo lo necesario para la tarea
4. **No borrar** lógica que funciona sin confirmar con el usuario
5. **Mostrar diff** o resumen después de modificar

### Prohibiciones explícitas
- No reactivar el menú viejo
- No romper el silencio humano (es una regla de negocio, no una feature)
- No depender solo del prompt para el aviso de emergencia — siempre reforzar por código con `asegurar_aviso_emergencia()`
- No hacer cambios grandes de arquitectura sin discutir primero

---

## 7. Comandos útiles

```bash
# Test sin WhatsApp (chat interactivo en terminal)
python tests/test_local.py

# Arrancar servidor local
uvicorn agent.main:app --reload --port 8000

# Instalar dependencias
pip install -r requirements.txt

# Ver logs en Railway
railway logs
```

---

## 8. Variables de entorno

```env
# Ollama
OLLAMA_API_KEY=...

# Z-API
ZAPI_INSTANCE_ID=...
ZAPI_TOKEN=...
ZAPI_CLIENT_TOKEN=...    # opcional

# Grupo interno de técnicos
WHAPI_GROUP_ID=...       # formato: {id}@g.us o {id}-group

# Groq (transcripción de audios)
GROQ_API_KEY=...

# Servidor
PORT=8000
ENVIRONMENT=development  # development | production

# Base de datos
DATABASE_URL=sqlite+aiosqlite:///./agentkit.db   # local
# DATABASE_URL=postgresql+asyncpg://...           # Railway (producción)
```
