# WhatsApp Bot Response Time Agent

Agente autónomo que abre WhatsApp Web, envía mensajes a chatbots programados
y mide su tiempo de respuesta, guardando todo en un dashboard visual.

---

## 📁 Archivos del proyecto

```
whatsapp-agent/
├── agent.py          ← Agente principal (Playwright + scheduler)
├── contacts.csv      ← Lista de bots a probar y sus horarios
├── dashboard.html    ← Dashboard visual de resultados
├── requirements.txt  ← Dependencias Python
├── results.json      ← Generado automáticamente por el agente
├── agent.log         ← Log de ejecución
└── wa_session/       ← Sesión del navegador (QR solo 1 vez)
```

---

## ⚡ Instalación

```bash
# 1. Crea un entorno virtual
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

# 2. Instala dependencias
pip install -r requirements.txt

# 3. Instala el navegador Chromium
playwright install chromium
```

---

## 📋 Configura tus bots en contacts.csv

```csv
phone,name,scheduled_time,message
573001234567,Bot Pedidos Corona,14:00,Hola
573009876543,Bot Soporte Corona,14:01,Hola
573001112233,Bot Marketing,09:30,Hola
```

| Campo            | Descripción                                      |
|------------------|--------------------------------------------------|
| `phone`          | Número completo con código de país (ej: 57...)   |
| `name`           | Nombre descriptivo del bot                       |
| `scheduled_time` | Hora diaria de prueba en formato HH:MM (24h)     |
| `message`        | Mensaje que le enviará el agente                 |

---

## 🚀 Uso

### Modo programado (recomendado para producción)
```bash
python agent.py
# Ejecutará cada bot en su hora configurada, todos los días
```

### Modo inmediato (para pruebas)
```bash
python agent.py --run-now
# Ejecuta todas las pruebas sin importar el horario
```

### CSV personalizado
```bash
python agent.py --csv mis_bots.csv
python agent.py --csv mis_bots.csv --run-now
```

---

## 📱 Primera ejecución: escanear QR

Al ejecutar por primera vez se abrirá Chrome con WhatsApp Web.
Escanea el QR con tu teléfono. La sesión queda guardada en `wa_session/`
y no necesitarás escanear de nuevo (hasta que WhatsApp cierre la sesión).

---

## 📊 Ver el dashboard

**Opción A — Abrir directo:**
Doble clic en `dashboard.html` → Arrastra `results.json` al área de carga.

**Opción B — Con servidor local (auto-carga):**
```bash
cd whatsapp-agent
python -m http.server 8080
# Abre http://localhost:8080/dashboard.html
# Los resultados se actualizan automáticamente cada 30s
```

---

## ⚙️ Parámetros ajustables (en agent.py)

| Variable           | Valor por defecto | Descripción                              |
|--------------------|-------------------|------------------------------------------|
| `MAX_WAIT_SECONDS` | 120               | Segundos máximos esperando respuesta     |
| `POLL_INTERVAL`    | 2                 | Cada cuántos segundos revisa mensajes    |

---

## 📄 Formato de results.json

```json
[
  {
    "id": "573001234567_20250527_140000",
    "phone": "573001234567",
    "name": "Bot Pedidos Corona",
    "message_sent": "Hola",
    "timestamp": "2025-05-27T14:00:05.123456",
    "status": "responded",
    "response_time_seconds": 3.4,
    "bot_response": "¡Hola! ¿En qué puedo ayudarte?",
    "error": null
  }
]
```

**Estados posibles:**
- `responded` — El bot respondió ✅
- `timeout` — No respondió en el tiempo máximo ⏱️
- `error` — Error técnico ❌

---

## 🔒 Notas importantes

- WhatsApp Web requiere que el teléfono esté conectado a internet.
- El agente corre localmente; ningún dato sale de tu computador.
- Para múltiples cuentas de WhatsApp, usa distintos `wa_session/` y ejecuta instancias separadas.
