"""Corrida completa sin red: reemplaza Yahoo, EDGAR y FRED por datos sintéticos
con la misma forma que devuelven las APIs reales, y genera la página."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from radar import datos, edgar, main


def _fake_yf():
    anios = pd.to_datetime([f"{y}-12-31" for y in range(2021, 2026)])

    def estados(base_gan, crec, deuda=200.0):
        res = pd.DataFrame({t: {"TotalRevenue": 10 * base_gan * crec ** i, "NetIncomeCommonStockholders": base_gan * crec ** i,
                                "DilutedEPS": base_gan / 100 * crec ** i, "EBITDA": 2 * base_gan * crec ** i}
                            for i, t in enumerate(reversed(anios))}).iloc[:, ::-1]
        bal = pd.DataFrame({t: {"CurrentAssets": 900.0, "CurrentLiabilities": 400.0, "LongTermDebt": deuda, "TotalDebt": deuda + 50,
                                "StockholdersEquity": 1200.0, "TotalAssets": 3000.0, "CashAndCashEquivalents": 300.0,
                                "Inventory": 100.0} for t in anios})
        return res, bal

    perfiles = {
        "AAA": dict(info={"quoteType": "EQUITY", "longName": "Crece Rápido SA", "sector": "Technology", "industry": "Software - Application",
                          "currency": "USD", "financialCurrency": "USD", "currentPrice": 50.0, "marketCap": 5000.0,
                          "trailingPE": 25.0, "totalRevenue": 3000.0, "netIncomeToCommon": 200.0, "heldPercentInstitutions": 0.3},
                    est=(0.30, 0.28, 0.27), gan=(100.0, 1.25)),
        "KOX": dict(info={"quoteType": "EQUITY", "longName": "Bebidas Estables Inc", "sector": "Consumer Defensive", "industry": "Beverages - Non-Alcoholic",
                          "currency": "USD", "financialCurrency": "USD", "currentPrice": 60.0, "marketCap": 26000.0,
                          "trailingPE": 26.0, "totalRevenue": 4000.0, "netIncomeToCommon": 1000.0},
                    est=(0.06, 0.07, 0.065), gan=(800.0, 1.06), div=0.5),
        "SEMI": dict(info={"quoteType": "EQUITY", "longName": "Memorias Cíclicas Corp", "sector": "Technology", "industry": "Semiconductors",
                           "currency": "USD", "financialCurrency": "TWD", "currentPrice": 100.0, "marketCap": 8000.0,
                           "trailingPE": 9.0, "totalRevenue": 90000.0, "netIncomeToCommon": 28000.0},
                     est=(0.5, -0.1, None), gan=(-50.0, 1.0)),
        "ETFX": dict(info={"quoteType": "ETF", "longName": "Índice de Prueba ETF", "currency": "USD", "currentPrice": 500.0, "trailingPE": 24.0}),
    }

    def download(tickers, **kw):
        idx = pd.bdate_range(end="2026-10-30", periods=300)
        cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
        rng = np.random.default_rng(1)
        df = pd.DataFrame(index=idx, columns=cols, dtype=float)
        for t in tickers:
            p = perfiles[t]["info"]["currentPrice"]
            serie = p * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx))))
            serie = serie / serie[-1] * p
            for c in ["Open", "High", "Low", "Close"]:
                df[(t, c)] = serie
        return df

    class Ticker:
        def __init__(self, sym):
            self.p = perfiles.get(sym, {"info": {}})
            self.info = self.p["info"]
            fechas = pd.date_range("2000-03-01", "2026-09-01", freq="QS").tz_localize("America/New_York")
            self.dividends = pd.Series(self.p.get("div", 0.0), index=fechas) if self.p.get("div") else pd.Series(dtype=float)
            self.splits = pd.Series(dtype=float)
            e = self.p.get("est")
            self.earnings_estimate = pd.DataFrame({"growth": [e[0], e[1]]}, index=["0y", "+1y"]) if e else pd.DataFrame()
            self.growth_estimates = pd.DataFrame({"stockTrend": [e[2]]}, index=["+5y"]) if e and e[2] else pd.DataFrame()
            if "gan" in self.p:
                self._res, self._bal = estados(*self.p["gan"])

        def get_income_stmt(self, **kw):
            return self._res

        def get_balance_sheet(self, freq="yearly", **kw):
            return self._bal if freq == "yearly" else pd.DataFrame()

        def history(self, **kw):  # tipo de cambio
            return pd.DataFrame({"Close": [32.0, 32.5]}, index=pd.to_datetime(["2020-01-01", "2026-10-29"]))

    return SimpleNamespace(download=download, Ticker=Ticker), list(perfiles)


def test_corrida_completa(tmp_path, monkeypatch):
    fake, tickers = _fake_yf()
    monkeypatch.setattr(datos, "yf", fake)
    monkeypatch.setattr(datos, "tasa_aaa", lambda r: (5.3, "test"))
    monkeypatch.setattr(main, "tasa_aaa", lambda r, s=0.9: (5.3, "test"))
    monkeypatch.setattr(edgar.Edgar, "historia", lambda self, t, s, m=None: None)
    monkeypatch.setattr(main, "leer_watchlist", lambda p: [{"ticker": t} for t in tickers])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    main.main(["--salida", str(tmp_path)])

    data = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    por = {r["ticker"]: r for r in data["acciones"]}
    assert set(por) == set(tickers) and not data["errores"]
    assert por["ETFX"]["senal"] == "N/A"
    assert por["AAA"]["lynch"]["categoria"] == "rapido"
    assert por["KOX"]["lynch"]["categoria"] in ("estable", "lento")
    assert por["KOX"]["metricas"]["racha_dividendos"] >= 20
    assert por["SEMI"]["lynch"]["categoria"] == "recuperacion"   # pérdidas en la serie
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "__DATOS__" not in html
    assert (tmp_path / "historial.csv").exists()

    # Segunda corrida "al día siguiente" con un cambio forzado: debe detectarlo
    prev = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    prev["fecha_datos"] = "2026-10-29"
    prev["acciones"][0]["senal"] = "VENDER" if prev["acciones"][0]["senal"] != "VENDER" else "COMPRAR"
    (tmp_path / "data.json").write_text(json.dumps(prev), encoding="utf-8")
    main.main(["--salida", str(tmp_path)])
    data2 = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    assert any(c["marco"] == "Combinada" for c in data2["cambios"])
