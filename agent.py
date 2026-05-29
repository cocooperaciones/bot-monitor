"""
WhatsApp Response Time Agent - Secuencial optimizado
Una sola ventana, un chat a la vez.
Envia Hola, espera respuesta, si responde sigue, si no en 2 min reporta timeout y sigue.
"""

import argparse
import asyncio
import json
import logging
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
import schedule
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE_DIR         = Path(__file__).parent
RESULTS_FILE     = BASE_DIR / "results.json"
SESSION_DIR      = BASE_DIR / "wa_session"
MAX_WAIT_SECONDS = 120
POLL_INTERVAL    = 1
DISCORD_WEBHOOK  = "https://discord.com/api/webhooks/1509734885495935006/BvWbNfGFL2uwBBsF7kafOC_lpN0PaMAix_o1boTmdK4OncA7N2NoB9o71MyY_q_xWSoK"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "agent.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("wa-agent")


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
    log.info(f"Guardado: {entry['name']} -> {entry['status']} {entry['response_time_seconds']}s")


def build_wa_url(phone):
    clean = "".join(c for c in str(phone) if c.isdigit())
    return f"https://web.whatsapp.com/send?phone={clean}&text=&source=&data="


def send_discord_alert(name, phone, error):
    import subprocess
    message = {
        "embeds": [{
            "title": "Bot sin respuesta",
            "color": 16711680,
            "fields": [
                {"name": "Bot",    "value": name,                    "inline": True},
                {"name": "Numero", "value": phone,                   "inline": True},
                {"name": "Error",  "value": error or "Timeout 120s", "inline": False}
            ],
            "footer": {"text": "WhatsApp Bot Monitor - COCO"}
        }]
    }
    try:
        payload = json.dumps(message)
        subprocess.run([
            "curl", "-s", "-X", "POST", DISCORD_WEBHOOK,
            "-H", "Content-Type: application/json",
            "-d", payload
        ], capture_output=True)
        log.info(f"[{name}] Alerta enviada a Discord.")
    except Exception as e:
        log.error(f"Error enviando alerta a Discord: {e}")


async def probe_bot(page, phone, name, message):
    result = {
        "id": f"{phone}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "phone": phone, "name": name, "message_sent": message,
        "timestamp": datetime.now().isoformat(),
        "status": "error", "response_time_seconds": None,
        "bot_response": None, "error": None,
    }
    try:
        log.info(f"[{name}] Abriendo chat...")
        await page.goto(build_wa_url(phone), timeout=60_000)

        input_sel = 'div[contenteditable="true"][data-tab="10"]'
        try:
            await page.wait_for_selector(input_sel, timeout=30_000)
        except PWTimeout:
            result["error"] = "No se encontro el input del chat."
            send_discord_alert(name, phone, result["error"])
            return result

        # Esperar que carguen mensajes previos
        await asyncio.sleep(3)

        # Guardar texto del ultimo mensaje antes de enviar
        prev_msgs = await page.query_selector_all('div.message-in')
        before_count = len(prev_msgs)
        last_text_before = ""
        if prev_msgs:
            last_text_before = await prev_msgs[-1].inner_text()

        # Enviar mensaje UNA sola vez
        await page.click(input_sel)
        await page.type(input_sel, message, delay=60)
        await page.keyboard.press("Enter")
        send_time = time.monotonic()
        log.info(f"[{name}] Mensaje enviado. Esperando respuesta (max {MAX_WAIT_SECONDS}s)...")

        # Esperar respuesta - sale apenas detecta cambio
        deadline  = time.monotonic() + MAX_WAIT_SECONDS
        responded = False
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            current_msgs = await page.query_selector_all('div.message-in')
            if len(current_msgs) > before_count:
                elapsed  = time.monotonic() - send_time
                bot_text = await current_msgs[before_count].inner_text()
                result.update(
                    status="responded",
                    response_time_seconds=round(elapsed, 2),
                    bot_response=bot_text.strip()[:500],
                )
                log.info(f"[{name}] Respondio en {elapsed:.1f}s")
                responded = True
                break
            # Tambien detectar si el ultimo mensaje cambio (bots que editan su respuesta)
            if prev_msgs:
                current_last = await page.query_selector('div.message-in:last-child')
                if current_last:
                    current_text = await current_last.inner_text()
                    if current_text != last_text_before:
                        elapsed  = time.monotonic() - send_time
                        result.update(
                            status="responded",
                            response_time_seconds=round(elapsed, 2),
                            bot_response=current_text.strip()[:500],
                        )
                        log.info(f"[{name}] Respondio en {elapsed:.1f}s")
                        responded = True
                        break

        if not responded:
            result["status"] = "timeout"
            result["error"]  = f"Sin respuesta en {MAX_WAIT_SECONDS}s"
            log.warning(f"[{name}] TIMEOUT - enviando alerta a Discord")
            send_discord_alert(name, phone, result["error"])

        # Volver al inicio para el siguiente bot
        await page.goto("https://web.whatsapp.com", timeout=30_000)

    except Exception as exc:
        result["error"] = str(exc)
        log.error(f"[{name}] Error: {exc}")
        send_discord_alert(name, phone, str(exc))

    return result


async def run_probes(contacts):
    SESSION_DIR.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )

        page = await browser.new_page()
        await page.goto("https://web.whatsapp.com", timeout=60_000)
        log.info("Esperando WhatsApp Web...")
        try:
            await page.wait_for_selector('div[data-testid="chat-list"]', timeout=120_000)
            log.info("WhatsApp Web listo.")
        except PWTimeout:
            log.error("No se detecto sesion activa.")
            await browser.close()
            return

        for contact in contacts:
            result = await probe_bot(
                page,
                phone=str(contact["phone"]),
                name=str(contact.get("name", contact["phone"])),
                message=str(contact.get("message", "Hola")),
            )
            save_result(result)
            await asyncio.sleep(2)

        await browser.close()
        log.info("Todas las pruebas completadas.")

        import subprocess
        subprocess.run(
            ["bash", "/Users/coconataliacorrea/whatsapp-agent/push_results.sh"],
            capture_output=True
        )
        log.info("Resultados subidos a GitHub.")


def schedule_from_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str, encoding="latin-1")
    df["phone"]          = df["phone"].str.strip()
    df["scheduled_time"] = df["scheduled_time"].str.strip()

    groups = df.groupby("scheduled_time")
    for scheduled_time, group in groups:
        contacts = group.to_dict("records")

        def make_job(c):
            def job():
                log.info(f"Ejecutando {len(c)} prueba(s)...")
                asyncio.run(run_probes(c))
            return job

        schedule.every().day.at(scheduled_time).do(make_job(contacts))
        log.info(f"{len(contacts)} bot(s) programado(s) a las {scheduled_time}")

    log.info("Scheduler activo. Esperando horarios...")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",     default="contacts.csv")
    parser.add_argument("--run-now", action="store_true")
    args = parser.parse_args()

    csv_path = BASE_DIR / args.csv
    if not csv_path.exists():
        log.error(f"No se encontro: {csv_path}")
        exit(1)

    if args.run_now:
        df       = pd.read_csv(csv_path, dtype=str, encoding="latin-1")
        contacts = df.to_dict("records")
        log.info(f"Ejecutando {len(contacts)} prueba(s) ahora...")
        asyncio.run(run_probes(contacts))
    else:
        schedule_from_csv(str(csv_path))
