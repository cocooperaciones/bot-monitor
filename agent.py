"""
WhatsApp Response Time Agent
Envia Hola, mide tiempo de respuesta, alerta Discord si no responde en 2 min.
"""

import argparse
import asyncio
import json
import logging
import time
import urllib.request
import subprocess
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

        # Enviar mensaje
        await page.click(input_sel)
        await page.type(input_sel, message, delay=60)
        await page.keyboard.press("Enter")
        send_time = time.monotonic()
        log.info(f"[{name}] Mensaje enviado. Esperando respuesta (max {MAX_WAIT_SECONDS}s)...")

        # Esperar 2s antes de detectar
        await asyncio.sleep(2)

        # Detectar respuesta
        deadline  = time.monotonic() + MAX_WAIT_SECONDS
        responded = False
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                msgs = await page.query_selector_all('[data-testid="msg-container"]')
                for msg in reversed(msgs):
                    # Saltar mensajes enviados por nosotros
                    is_sent = await msg.query_selector('[data-testid="msg-dblcheck"]')
                    if is_sent:
                        continue
                    # Verificar que tiene meta (es mensaje del bot)
                    meta = await msg.query_selector('[data-testid="msg-meta"]')
                    if not meta:
                        continue
                    elapsed = time.monotonic() - send_time
                    # Ignorar mensajes previos al envio
                    if elapsed < 1.5:
                        continue
                    bot_text = await msg.inner_text()
                    if not bot_text.strip():
                        continue
                    result.update(
                        status="responded",
                        response_time_seconds=round(elapsed, 2),
                        bot_response=bot_text.strip()[:500],
                    )
                    log.info(f"[{name}] Respondio en {elapsed:.1f}s")
                    responded = True
                    break
            except Exception as ex:
                log.debug(f"[{name}] Error en deteccion: {ex}")
            if responded:
                break

        if not responded:
            result["status"] = "timeout"
            result["error"]  = f"Sin respuesta en {MAX_WAIT_SECONDS}s"
            log.warning(f"[{name}] TIMEOUT - enviando alerta a Discord")
            send_discord_alert(name, phone, result["error"])

        # Volver al inicio
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
        df = pd.read_csv(csv_path, dtype=str, encoding="latin-1")
        df = df.drop_duplicates(subset=["phone"])
        contacts = df.to_dict("records")
        log.info(f"Ejecutando {len(contacts)} prueba(s) ahora (una por bot)...")
        asyncio.run(run_probes(contacts))
    else:
        schedule_from_csv(str(csv_path))
