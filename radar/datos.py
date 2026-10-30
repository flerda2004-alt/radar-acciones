"""Descarga de datos de mercado y balances (Yahoo Finance vía yfinance) y tasa AAA (FRED)."""
from __future__ import annotations

import io
import logging
import math
import time

import pandas as pd
import requests
import yfinance as yf

log = logging.getLogger("radar")

# Monedas con inflación alta: las series históricas se pasan a USD con el tipo de cambio
# de cada cierre de ejercicio, si no el "crecimiento" es inflación.
ALTA_INFLACION = {"ARS", "TRY", "VES", "NGN", "EGP"}

# Yahoo cotiza algunas plazas en centavos
SUBUNIDADES = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100), "ZAc": ("ZAR", 100)}


def num(x):
    try:
        if x is None:
            return None
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def reintentar(fn, intentos=3, pausa=2.0, desc=""):
    for i in range(intentos):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == intentos - 1:
                log.warning("Falló %s: %s", desc, e)
                return None
            time.sleep(pausa * (2 ** i))
    return None


def _naive(idx):
    idx = pd.to_datetime(idx)
    return idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx


# ───────────────────────────── Precios ─────────────────────────────

def precios_lote(tickers: list[str]) -> dict[str, dict]:
    """Una sola descarga para todos los tickers: precio, variación diaria y retornos."""
    df = reintentar(
        lambda: yf.download(tickers, period="14mo", interval="1d", auto_adjust=True,
                            group_by="ticker", progress=False, threads=True),
        desc="descarga de precios",
    )
    out: dict[str, dict] = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            s = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
        except KeyError:
            continue
        s = s.dropna()
        if len(s) < 2:
            continue
        s.index = _naive(s.index)
        ult, fecha = float(s.iloc[-1]), s.index[-1]
        hace_1a = s[s.index <= fecha - pd.Timedelta(days=365)]
        cierre_anio_prev = s[s.index.year < fecha.year]
        ventana = s[s.index > fecha - pd.Timedelta(days=365)]
        out[t] = {
            "precio": ult,
            "fecha": fecha.strftime("%Y-%m-%d"),
            "var_dia": ult / float(s.iloc[-2]) - 1,
            "ret_1a": ult / float(hace_1a.iloc[-1]) - 1 if len(hace_1a) else None,
            "ret_ytd": ult / float(cierre_anio_prev.iloc[-1]) - 1 if len(cierre_anio_prev) else None,
            "max_52": float(ventana.max()),
            "min_52": float(ventana.min()),
        }
    return out


# ───────────────────────────── Tipo de cambio ─────────────────────────────

class TipoCambio:
    """USD por unidad de moneda, actual e histórico (pares de Yahoo 'XXX=X' = XXX por USD)."""

    def __init__(self):
        self._hist: dict[str, pd.Series | None] = {}

    def _serie(self, moneda: str) -> pd.Series | None:
        if moneda not in self._hist:
            h = reintentar(lambda: yf.Ticker(f"{moneda}=X").history(period="max", auto_adjust=False),
                           desc=f"tipo de cambio {moneda}")
            if h is None or h.empty:
                self._hist[moneda] = None
            else:
                s = h["Close"].dropna()
                s = s[s > 0]
                s.index = _naive(s.index)
                self._hist[moneda] = s
        return self._hist[moneda]

    def a_usd(self, moneda: str | None, fecha: pd.Timestamp | None = None) -> float | None:
        if not moneda or moneda == "USD":
            return 1.0
        div = 1.0
        if moneda in SUBUNIDADES:
            moneda, div = SUBUNIDADES[moneda]
        s = self._serie(moneda.upper())
        if s is None or s.empty:
            return None
        if fecha is not None:
            previo = s[s.index <= pd.Timestamp(fecha)]
            valor = float(previo.iloc[-1]) if len(previo) else float(s.iloc[0])
        else:
            valor = float(s.iloc[-1])
        return 1.0 / valor / div


# ───────────────────────────── Fundamentals ─────────────────────────────

def _df(fn):
    try:
        d = fn()
        return d if isinstance(d, pd.DataFrame) else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def datos_ticker(sym: str, reintentos: int = 3) -> dict:
    tk = yf.Ticker(sym)
    info = reintentar(lambda: tk.info, intentos=reintentos, desc=f"info {sym}") or {}
    d = {"info": info}
    d["dividendos"] = reintentar(lambda: tk.dividends, intentos=reintentos, desc=f"dividendos {sym}")
    if info.get("quoteType") == "ETF":
        return d
    d["resultados"] = _df(lambda: tk.get_income_stmt(freq="yearly", pretty=False))
    d["balance"] = _df(lambda: tk.get_balance_sheet(freq="yearly", pretty=False))
    d["balance_trim"] = _df(lambda: tk.get_balance_sheet(freq="quarterly", pretty=False))
    d["splits"] = reintentar(lambda: tk.splits, intentos=reintentos, desc=f"splits {sym}")
    d["est_ganancias"] = _df(lambda: tk.earnings_estimate)
    d["est_crecimiento"] = _df(lambda: tk.growth_estimates)
    return d


# ───────────────────────────── Tasa AAA ─────────────────────────────

def tasa_aaa(respaldo: float) -> tuple[float, str]:
    """Rendimiento de bonos corporativos AAA de Moody's (FRED). Graham usa 'Y' = AAA."""
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=AAA", timeout=30,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        serie = pd.to_numeric(df.iloc[:, -1], errors="coerce").dropna()
        if len(serie):
            return float(serie.iloc[-1]), f"FRED AAA ({df.iloc[serie.index[-1], 0]})"
    except Exception as e:  # noqa: BLE001
        log.warning("FRED no respondió (%s); uso tasa de respaldo", e)
    return respaldo, "valor de respaldo (config.yaml)"
