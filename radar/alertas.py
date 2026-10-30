"""Aviso opcional por Telegram cuando cambia una señal.

Configurar en GitHub → Settings → Secrets and variables → Actions:
  TELEGRAM_BOT_TOKEN  (lo da @BotFather al crear el bot)
  TELEGRAM_CHAT_ID    (tu chat o el de un grupo/canal de clientes)
Si no están, no hace nada.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("radar")
ICONO = {"COMPRAR": "🟢", "MANTENER": "🟡", "VENDER": "🔴"}


def telegram(data: dict):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    cambios = [c for c in data.get("cambios", []) if c["marco"] == "Combinada"]
    filas = data.get("acciones", [])
    cuenta = {s: sum(1 for r in filas if r["senal"] == s) for s in ICONO}
    lineas = [f"<b>{data['sitio'].get('titulo', 'Radar')}</b> — cierre {data['fecha_datos']}",
              " · ".join(f"{ICONO[s]} {s.title()}: {n}" for s, n in cuenta.items())]
    if cambios:
        lineas.append("\n<b>Cambios de señal</b>")
        for c in cambios:
            lineas.append(f"{c['ticker']}: {ICONO.get(c['antes'], '')} {c['antes'].title()} → "
                          f"{ICONO.get(c['ahora'], '')} {c['ahora'].title()}")
    else:
        lineas.append("Sin cambios de señal respecto del cierre anterior.")
    url = data["sitio"].get("url_publica")
    if url:
        lineas.append(f'\n<a href="{url}">Ver el radar completo</a>')
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": "\n".join(lineas), "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo enviar el aviso de Telegram: %s", e)
