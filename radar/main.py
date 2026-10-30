"""Punto de entrada: descarga, evalúa y publica.

Uso:
    python -m radar.main                 # corre toda la watchlist
    python -m radar.main --solo AAPL KO  # prueba con algunos tickers
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import alertas, graham, lynch, sitio
from .datos import TipoCambio, datos_ticker, precios_lote, tasa_aaa
from .edgar import Edgar
from .metricas import construir
from .reglas_comunes import NO_APLICA, combinar

RAIZ = Path(__file__).resolve().parent.parent
log = logging.getLogger("radar")


def leer_watchlist(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out, vistos = [], set()
    for item in data.get("acciones", []):
        d = {"ticker": item} if isinstance(item, str) else dict(item)
        d["ticker"] = str(d["ticker"]).strip().upper()
        if d["ticker"] not in vistos:
            vistos.add(d["ticker"])
            out.append(d)
    return out


def evaluar_ticker(item, raw, px, tc, edgar, cfg, aaa) -> dict:
    sym = item["ticker"]
    info = raw.get("info") or {}
    hist = None
    if info.get("quoteType") != "ETF" and edgar is not None and not sym.endswith(".BA"):
        try:
            hist = edgar.historia(sym, raw.get("splits"), info.get("financialCurrency") or info.get("currency"))
        except Exception as e:  # noqa: BLE001
            log.warning("EDGAR %s: %s", sym, e)
    m = construir(sym, raw, px, tc, hist, cfg)
    base = {
        "ticker": sym,
        "nombre": info.get("longName") or info.get("shortName") or sym,
        "tipo": m.get("tipo"),
        "sector": info.get("sector") or ("ETF" if m.get("tipo") == "ETF" else None),
        "industria": info.get("industry") or info.get("category"),
        "moneda": m.get("moneda"),
        "precio": px.get("precio") or m.get("precio"),
        "fecha_precio": px.get("fecha"),
        "var_dia": px.get("var_dia"), "ret_ytd": px.get("ret_ytd"), "ret_1a": px.get("ret_1a"),
        "max_52": px.get("max_52"), "min_52": px.get("min_52"),
    }
    if m.get("tipo") == "ETF":
        return {**base, "senal": NO_APLICA,
                "motivo": "ETF: Lynch y Graham evalúan empresas, no índices",
                "lynch": None, "graham": None, "metricas": m, "alertas": m.get("alertas", [])}
    ev_l = lynch.evaluar(m, cfg, item.get("categoria_lynch"))
    ev_g = graham.evaluar(m, cfg, aaa)
    senal, motivo = combinar(ev_l["senal"], ev_g["senal"])
    return {**base, "senal": senal, "motivo": motivo, "lynch": ev_l, "graham": ev_g,
            "metricas": m, "alertas": m.get("alertas", []) + ev_l.get("alertas", [])}


def detectar_cambios(previo: dict | None, actuales: list[dict]) -> list[dict]:
    if not previo:
        return []
    antes = {r["ticker"]: r for r in previo.get("acciones", [])}
    cambios = []
    for r in actuales:
        a = antes.get(r["ticker"])
        if not a:
            continue
        for clave, nombre in (("senal", "Combinada"), ("lynch", "Lynch"), ("graham", "Graham")):
            va = a.get(clave) if clave == "senal" else (a.get(clave) or {}).get("senal")
            vn = r.get(clave) if clave == "senal" else (r.get(clave) or {}).get("senal")
            if va and vn and va != vn:
                cambios.append({"ticker": r["ticker"], "marco": nombre, "antes": va, "ahora": vn,
                                "desde": previo.get("fecha_datos")})
    return cambios


def guardar_historial(path: Path, fecha: str, filas: list[dict]):
    existentes = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            existentes = [r for r in csv.DictReader(f) if r.get("fecha") != fecha]
    campos = ["fecha", "ticker", "precio", "senal", "lynch", "graham", "categoria_lynch", "per", "peg"]
    nuevas = []
    for r in filas:
        m = r.get("metricas") or {}
        nuevas.append({
            "fecha": fecha, "ticker": r["ticker"],
            "precio": round(r["precio"], 4) if r.get("precio") else "",
            "senal": r["senal"],
            "lynch": (r.get("lynch") or {}).get("senal", ""),
            "graham": (r.get("graham") or {}).get("senal", ""),
            "categoria_lynch": (r.get("lynch") or {}).get("categoria", ""),
            "per": round(m["per"], 2) if m.get("per") else "",
            "peg": round(m["peg"], 2) if m.get("peg") else "",
        })
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(existentes + nuevas)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", nargs="*", help="evaluar solo estos tickers")
    ap.add_argument("--salida", default=str(RAIZ / "docs"))
    ap.add_argument("--sin-edgar", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))
    watch = leer_watchlist(RAIZ / "watchlist.yaml")
    if args.solo:
        pedidos = {t.upper() for t in args.solo}
        watch = [w for w in watch if w["ticker"] in pedidos] or [{"ticker": t} for t in pedidos]
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)

    aaa, fuente_aaa = tasa_aaa(cfg["datos"]["tasa_aaa_respaldo"], cfg["datos"].get("spread_aaa_sobre_tesoro", 0.9))
    log.info("Tasa AAA: %.2f%% (%s)", aaa, fuente_aaa)
    tickers = [w["ticker"] for w in watch]
    precios = precios_lote(tickers)
    log.info("Precios descargados: %d/%d", len(precios), len(tickers))

    ua = os.environ.get("SEC_USER_AGENT") or cfg["datos"]["sec_user_agent"]
    edgar = None if args.sin_edgar else Edgar(ua, cfg["datos"]["reintentos"])
    tc = TipoCambio()

    filas, errores = [], []
    for i, item in enumerate(watch, 1):
        sym = item["ticker"]
        log.info("[%d/%d] %s", i, len(watch), sym)
        try:
            raw = datos_ticker(sym, cfg["datos"]["reintentos"])
            if not raw.get("info") and sym not in precios:
                raise RuntimeError("Yahoo no devolvió datos")
            filas.append(evaluar_ticker(item, raw, precios.get(sym, {}), tc, edgar, cfg, aaa))
        except Exception as e:  # noqa: BLE001
            log.error("%s: %s\n%s", sym, e, traceback.format_exc())
            errores.append({"ticker": sym, "error": str(e)})
        time.sleep(cfg["datos"]["pausa_entre_tickers_seg"])

    if len(filas) < max(1, len(watch) // 2):
        log.error("Fallaron %d de %d tickers: no se publica para no pisar datos buenos", len(errores), len(watch))
        sys.exit(1)

    fechas = sorted({f["fecha_precio"] for f in filas if f.get("fecha_precio")})
    fecha_datos = fechas[-1] if fechas else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    previo = None
    pj = salida / "data.json"
    if pj.exists():
        try:
            previo = json.loads(pj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previo = None
    cambios = detectar_cambios(previo, filas)
    if previo and previo.get("fecha_datos") == fecha_datos:
        # Re-corrida del mismo día: conservar los cambios ya detectados contra el día anterior
        cambios = previo.get("cambios", []) if not cambios else cambios

    ahora = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    data = {
        "generado": ahora.strftime("%Y-%m-%d %H:%M"),
        "fecha_datos": fecha_datos,
        "tasa_aaa": aaa, "fuente_aaa": fuente_aaa,
        "sitio": cfg["sitio"],
        "parametros": {"lynch": {k: v for k, v in cfg["lynch"].items() if k != "industrias_ciclicas"},
                       "graham": cfg["graham"]},
        "cambios": cambios,
        "errores": errores,
        "acciones": filas,
    }
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    guardar_historial(salida / "historial.csv", fecha_datos, filas)
    sitio.construir(data, salida / "index.html")
    log.info("Listo: %d acciones, %d cambios de señal, %d errores", len(filas), len(cambios), len(errores))

    if not args.solo:
        alertas.telegram(data)


if __name__ == "__main__":
    main()
