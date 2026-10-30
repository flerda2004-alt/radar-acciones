"""Historia anual de EPS y ganancia neta desde SEC EDGAR (XBRL).

Yahoo solo entrega 4-5 ejercicios; Graham pide 10 años de ganancias.
EDGAR tiene la serie completa de cada 10-K / 20-F presentado.

Detalle importante: los EPS viejos no están ajustados por splits posteriores
(ej. NVDA 10:1 en 2024). Cada dato trae su fecha de presentación ("filed"),
así que se divide por todos los splits ocurridos DESPUÉS de esa fecha.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import pandas as pd
import requests

log = logging.getLogger("radar")

URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"
URL_CONCEPTO = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{tax}/{tag}.json"

FORMULARIOS_ANUALES = {"10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F", "40-F/A"}

TAGS_EPS = [
    ("us-gaap", "EarningsPerShareDiluted"),
    ("us-gaap", "EarningsPerShareBasicAndDiluted"),
    ("us-gaap", "EarningsPerShareBasic"),
    ("ifrs-full", "DilutedEarningsLossPerShare"),
    ("ifrs-full", "BasicEarningsLossPerShare"),
    ("ifrs-full", "BasicAndDilutedEarningsLossPerShare"),
]
TAGS_GANANCIA = [
    ("us-gaap", "NetIncomeLoss"),
    ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    ("us-gaap", "ProfitLoss"),
    ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
    ("ifrs-full", "ProfitLoss"),
]


def _fecha(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


class Edgar:
    def __init__(self, user_agent: str, reintentos: int = 3):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.reintentos = reintentos
        self._ciks: dict[str, int] | None = None
        self._ultimo_pedido = 0.0

    def _get(self, url: str):
        for i in range(self.reintentos):
            # La SEC permite 10 pedidos por segundo; vamos holgados.
            espera = 0.15 - (time.time() - self._ultimo_pedido)
            if espera > 0:
                time.sleep(espera)
            self._ultimo_pedido = time.time()
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                if i == self.reintentos - 1:
                    log.warning("EDGAR falló %s: %s", url, e)
                    return None
                time.sleep(1.5 * (2 ** i))
        return None

    def cik(self, ticker: str) -> int | None:
        if self._ciks is None:
            data = self._get(URL_TICKERS) or {}
            self._ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        t = ticker.upper()
        return self._ciks.get(t) or self._ciks.get(t.replace("-", ".")) or self._ciks.get(t.replace("-", ""))

    def _serie(self, cik: int, tags, moneda_pref: str | None = None) -> tuple[list[dict], str | None]:
        """Une varios tags (las empresas cambian de tag con los años).
        Devuelve [{fin, valor, presentado}] y la unidad (ej. 'USD/shares').
        Si la empresa informa en varias monedas (ej. TSMC: TWD y conversión a USD),
        se prefiere la moneda de sus balances."""
        por_fin: dict[date, dict] = {}
        unidad_ref = None
        for tax, tag in tags:
            js = self._get(URL_CONCEPTO.format(cik=cik, tax=tax, tag=tag))
            if not js:
                continue
            unidades = js.get("units", {})
            if not unidades:
                continue
            if unidad_ref is None:
                pref = [u for u in unidades if moneda_pref and u.upper().split("/")[0] == moneda_pref.upper()]
                unidad_ref = pref[0] if pref else max(unidades, key=lambda u: len(unidades[u]))
            if unidad_ref not in unidades:
                continue
            candidatos: dict[date, dict] = {}
            for f in unidades[unidad_ref]:
                if f.get("form") not in FORMULARIOS_ANUALES or not f.get("start") or not f.get("end"):
                    continue
                ini, fin = _fecha(f["start"]), _fecha(f["end"])
                if not 330 <= (fin - ini).days <= 380:
                    continue  # solo ejercicios completos
                pres = _fecha(f["filed"])
                prev = candidatos.get(fin)
                # Para cada ejercicio, el dato presentado más recientemente (re-expresado)
                if prev is None or pres > prev["presentado"]:
                    candidatos[fin] = {"fin": fin, "valor": float(f["val"]), "presentado": pres}
            for fin, d in candidatos.items():
                por_fin.setdefault(fin, d)
        return [por_fin[k] for k in sorted(por_fin)], unidad_ref

    def historia(self, ticker: str, splits: pd.Series | None, moneda_pref: str | None = None) -> dict | None:
        cik = self.cik(ticker)
        if cik is None:
            return None
        eps, u_eps = self._serie(cik, TAGS_EPS, moneda_pref)
        ganancia, u_gan = self._serie(cik, TAGS_GANANCIA, moneda_pref)
        if not eps and not ganancia:
            return None
        eps = ajustar_por_splits(eps, splits)
        moneda = None
        for u in (u_eps, u_gan):
            if u:
                moneda = u.split("/")[0].upper()
                break
        return {
            "fuente": "SEC EDGAR",
            "cik": cik,
            "moneda": moneda,
            "eps": pd.Series({pd.Timestamp(d["fin"]): d["valor"] for d in eps}, dtype=float).sort_index(),
            "ganancia": pd.Series({pd.Timestamp(d["fin"]): d["valor"] for d in ganancia}, dtype=float).sort_index(),
        }


def ajustar_por_splits(eps: list[dict], splits: pd.Series | None) -> list[dict]:
    """Divide cada EPS por los splits posteriores a su fecha de presentación."""
    if splits is None or len(splits) == 0:
        return eps
    lista = []
    for ts, ratio in splits.items():
        try:
            r = float(ratio)
        except (TypeError, ValueError):
            continue
        if r > 0 and r != 1:
            lista.append((pd.Timestamp(ts).tz_localize(None).date() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).date(), r))
    out = []
    for d in eps:
        factor = 1.0
        for fecha_split, r in lista:
            if fecha_split > d["presentado"]:
                factor *= r
        out.append({**d, "valor": d["valor"] / factor})
    return out
