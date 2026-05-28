"""
WhatsApp Response Time Agent
============================
Abre WhatsApp Web en una sola ventana, navega entre chats,
mide el tiempo de respuesta del chatbot, envía "exit" para
cerrar la conversación y guarda los resultados en results.json.

Requisitos:
    pip3 install -r requirements.txt
    python3 -m playwright install chromium

Uso:
    python3 agent.py                    # usa contacts.csv, modo programado
    python3 agent.py --csv mis_bots.csv
    python3 agent.py --run-now          # ejecuta de inmediato
"""

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import schedule
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
RESULTS_FILE = BASE_DIR / "results.json"
SESSION_DIR = BASE_DIR / "wa_session"
MAX_WAIT_SECONDS = 120
POLL_INTERVAL = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "agent.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("wa-agent")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_results():
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_result(entry):
    results = load_results()
    results.append(entry)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info(f"Resultado guardado: {entry}")


def build_wa_url(phone):
    clean = "".join(c for c in str(phone) if c.isdigit())
    return f"https://web.whatsapp.com/send?phone={clean}&text=&source=&data="


# ─── Core async task ──────────────────────────────────────────────────────────

async def probe_bot(page, phone, name, message):
    """
    Reutiliza la misma página: navega al chat, envía el mensaje,
    espera la respuesta, envía 'exit' y retorna el resultado.
    """
    result = {
        "id": f"{phone}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "phone": phone,
        "name": name,
        "message_sent": message,
        "timestamp": datetime.now().isoformat(),
        "status": "error",
        "response_time_seconds": None,
        "bot_response": None,
        "error": None,
    }

    try:
        url = build_wa_url(phone)
        log.info(f"[{name}] Navegando a {url}")
        await page.goto(url, timeout=60_000)

        input_sel = 'div[contenteditable="true"][data-tab="10"]'
        try:
            await page.wait_for_selector(input_sel, timeout=30_000)
        except PWTimeout:
            result["error"] = "No se encontró el input del chat. ¿Número inválido?"
            return result

        # Contar mensajes anteriores para detectar la respuesta nueva
        msg_sel = 'div.message-in .copyable-text'
        before_msgs = await page.query_selector_all(msg_sel)
        before_count = len(before_msgs)

        # Enviar mensaje inicial
        await page.click(input_sel)
        await page.type(input_sel, message, delay=60)
        await page.keyboard.press("Enter")

        send_time = time.monotonic()
        result["status"] = "sent_waiting"
        log.info(f"[{name}] Mensaje enviado. Esperando respuesta (máx {MAX_WAIT_SECONDS}s)…")

        # Polling: esperar mensaje nuevo entrante
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        responded = False
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            current_msgs = await page.query_selector_all(msg_sel)
            if len(current_msgs) > before_count:
                elapsed = time.monotonic() - send_time
                last_el = current_msgs[-1]
                bot_text = await last_el.inner_text()
                result.update(
                    status="responded",
                    response_time_seconds=round(elapsed, 2),
                    bot_response=bot_text.strip()[:500],
                )
                log.info(f"[{name}] Respuesta en {elapsed:.1f}s: «{bot_text[:80]}»")
                responded = True
                break

        if not responded:
            result["status"] = "timeout"
            result["error"] = f"Sin respuesta en {MAX_WAIT_SECONDS}s"
            log.warning(f"[{name}] Timeout esperando respuesta.")
        else:
            # Enviar "exit" para cerrar la conversación
            await asyncio.sleep(1)
            await page.click(input_sel)
            await page.type(input_sel, "exit", delay=60)
            await page.keyboard.press("Enter")
            log.info(f"[{name}] 'exit' enviado para cerrar conversación.")

    except Exception as exc:
        result["error"] = str(exc)
        log.error(f"[{name}] Error: {exc}")

    return result


# ─── Run all probes ───────────────────────────────────────────────────────────

async def run_probes(contacts):
    """Abre UNA sola ventana y navega entre chats para todas las pruebas."""
    SESSION_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )

        # Una sola página para todo el proceso
        page = await browser.new_page()
        await page.goto("https://web.whatsapp.com", timeout=60_000)
        log.info("Esperando que WhatsApp Web cargue / escanear QR si es primera vez…")

        try:
            await page.wait_for_selector('div[data-testid="chat-list"]', timeout=120_000)
            log.info("WhatsApp Web listo.")
        except PWTimeout:
            log.error("No se detectó sesión activa. Escanea el QR y vuelve a intentar.")
            await browser.close()
            return

        # Reutilizar la misma página para cada bot
        for contact in contacts:
            result = await probe_bot(
                page,
                phone=str(contact["phone"]),
                name=str(contact.get("name", contact["phone"])),
                message=str(contact.get("message", "Hola")),
            )
            save_result(result)
            await asyncio.sleep(3)

        await browser.close()
        log.info("Todas las pruebas completadas.")


# ─── Scheduler ────────────────────────────────────────────────────────────────

def schedule_from_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str, encoding="latin-1")
    df["phone"] = df["phone"].str.strip()
    df["scheduled_time"] = df["scheduled_time"].str.strip()

    groups = df.groupby("scheduled_time")
    for scheduled_time, group in groups:
        contacts = group.to_dict("records")

        def make_job(c):
            def job():
                log.info(f"Ejecutando prueba programada para {len(c)} contacto(s)…")
                asyncio.run(run_probes(c))
            return job

        schedule.every().day.at(scheduled_time).do(make_job(contacts))
        log.info(f"{len(contacts)} contacto(s) programado(s) para las {scheduled_time}")

    log.info("Scheduler iniciado. Esperando horarios… (Ctrl+C para detener)")
    while True:
        schedule.run_pending()
        time.sleep(10)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhatsApp Bot Response Time Agent")
    parser.add_argument("--csv", default="contacts.csv", help="Ruta al archivo CSV")
    parser.add_argument("--run-now", action="store_true", help="Ejecutar de inmediato")
    args = parser.parse_args()

    csv_path = BASE_DIR / args.csv
    if not csv_path.exists():
        log.error(f"No se encontró el archivo: {csv_path}")
        exit(1)

    if args.run_now:
        df = pd.read_csv(csv_path, dtype=str, encoding="latin-1")
        contacts = df.to_dict("records")
        log.info(f"Ejecutando {len(contacts)} prueba(s) ahora…")
        asyncio.run(run_probes(contacts))
    else:
        schedule_from_csv(str(csv_path))
