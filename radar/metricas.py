"""Construye las métricas de cada acción en forma consistente entre monedas.

Regla de oro: los ratios de precio se calculan como capitalización (USD) sobre
magnitudes del balance (convertidas a USD). Así se evita el problema de los ADR
(TSM = 5 acciones, BABA = 8) y de los balances en otra moneda (TWD, EUR, CNY, ARS).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

import pandas as pd

from .datos import ALTA_INFLACION, TipoCambio, num


# ───────────────────────────── utilidades de series ─────────────────────────────

def serie(df: pd.DataFrame | None, claves: list[str]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for k in claves:
        if k in df.index:
            fila = df.loc[k]
            if isinstance(fila, pd.DataFrame):  # índice duplicado
                fila = fila.iloc[0]
            s = pd.to_numeric(fila, errors="coerce").dropna()
            if not s.empty:
                idx = pd.to_datetime(s.index)
                s.index = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
                return s.sort_index()
    return pd.Series(dtype=float)


def ultimo(s: pd.Series):
    return float(s.iloc[-1]) if s is not None and len(s) else None


def anios_entre(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return (b - a).days / 365.25


def cagr(v0, v1, anios):
    if v0 is None or v1 is None or v0 <= 0 or v1 <= 0 or anios <= 0:
        return None
    return ((v1 / v0) ** (1 / anios) - 1) * 100


def crecimiento_historico(eps: pd.Series, objetivo=5.0, minimo=2.5):
    """CAGR del EPS entre el último ejercicio y el más cercano a 'objetivo' años antes."""
    if len(eps) < 2:
        return None, None
    fin, v1 = eps.index[-1], float(eps.iloc[-1])
    previos = [(abs(anios_entre(t, fin) - objetivo), t) for t in eps.index[:-1]
               if anios_entre(t, fin) >= minimo]
    if not previos:
        return None, None
    _, t0 = min(previos)
    if (eps[eps.index >= t0] <= 0).any():
        return None, None  # una tasa compuesta que atraviesa años con pérdidas no tiene sentido
    return cagr(float(eps[t0]), v1, anios_entre(t0, fin)), round(anios_entre(t0, fin))


def caida_maxima(vals: list[float]) -> float:
    peor = 0.0
    for a, b in zip(vals, vals[1:]):
        if a > 0:
            peor = max(peor, (a - b) / a)
    return peor


def ajustar_splits_yahoo(eps: pd.Series, ganancia: pd.Series, splits) -> pd.Series:
    """Yahoo a veces no re-expresa EPS viejos por splits. Se detecta por el salto en
    acciones implícitas (ganancia / EPS) alrededor de la fecha del split."""
    if splits is None or len(splits) == 0 or len(eps) < 2 or len(ganancia) < 2:
        return eps
    eps = eps.copy()
    comunes = eps.index.intersection(ganancia.index)
    if len(comunes) < 2:
        return eps
    acciones = (ganancia[comunes] / eps[comunes]).replace([float("inf"), -float("inf")], float("nan")).dropna()
    for ts, r in splits.items():
        r = num(r)
        if not r or r == 1:
            continue
        f = pd.Timestamp(ts)
        f = f.tz_localize(None) if f.tzinfo else f
        antes = acciones[acciones.index < f]
        despues = acciones[acciones.index >= f]
        if len(antes) and len(despues) and antes.iloc[-1] > 0:
            salto = despues.iloc[0] / antes.iloc[-1]
            if abs(salto / r - 1) < 0.25:  # el salto coincide con el split → no estaba ajustado
                eps[eps.index < f] = eps[eps.index < f] / r
                acciones[acciones.index < f] = acciones[acciones.index < f] * r
    return eps


def cortar_quiebres(s: pd.Series, salto: float = 10.0, nivel: float = 8.0, min_restante: int = 4) -> tuple[pd.Series, bool]:
    """Detecta cambios de base en la serie de la SEC (moneda o cantidad de acciones mal etiquetadas,
    ej. Pampa informaba en ARS antes de 2019; Nu antes de su IPO) y descarta la parte vieja.
    Un quiebre es un salto > 'salto' veces entre años consecutivos que además cambia el NIVEL
    de la serie (mediana de los 2 años previos vs. los 3 siguientes > 'nivel' veces).
    Así no se corta una caída real de ganancias que después se recupera (ej. Berkshire 2018)."""
    if s is None or len(s) < min_restante + 1:
        return s, False
    v = [float(x) for x in s.values]
    for i in range(len(v) - 1, 0, -1):
        a, b = abs(v[i - 1]), abs(v[i])
        if a == 0 or b == 0:
            continue
        if max(a, b) / min(a, b) <= salto:
            continue
        viejos = [abs(x) for x in v[max(0, i - 2):i] if x]
        nuevos = [abs(x) for x in v[i:i + 3] if x]
        if not viejos or not nuevos:
            continue
        r = statistics.median(viejos) / statistics.median(nuevos)
        if (r > nivel or r < 1 / nivel) and len(v) - i >= min_restante:
            return s.iloc[i:], True
    return s, False


def empalmar(base: pd.Series, extra: pd.Series, tolerancia: float = 0.15) -> tuple[pd.Series, int]:
    """Agrega a 'base' (SEC, historia larga) los ejercicios más nuevos de 'extra' (Yahoo).
    La escala se calcula en los años que se superponen (ej. 1 ADR de TSMC = 5 acciones)
    y solo se empalma si esa relación es estable entre años.
    Devuelve la serie y la cantidad de ejercicios agregados."""
    if base is None or len(base) == 0 or extra is None or len(extra) == 0:
        return base, 0
    ratios = []
    for t, v in extra.items():
        cerca = base[(base.index >= t - pd.Timedelta(days=20)) & (base.index <= t + pd.Timedelta(days=20))]
        if len(cerca) and v and abs(v) > 1e-9 and cerca.iloc[0] and (cerca.iloc[0] > 0) == (v > 0):
            ratios.append(float(cerca.iloc[0]) / float(v))
    if not ratios:
        return base, 0
    r = statistics.median(ratios)
    if r <= 0 or any(abs(x / r - 1) > tolerancia for x in ratios):
        return base, 0
    nuevos = extra[extra.index > base.index[-1] + pd.Timedelta(days=200)] * r
    if not len(nuevos):
        return base, 0
    return pd.concat([base, nuevos]).sort_index(), len(nuevos)


def a_lista(s: pd.Series, decimales=4):
    return [[t.strftime("%Y-%m-%d"), round(float(v), decimales)] for t, v in s.items()]


# ───────────────────────────── construcción ─────────────────────────────

def construir(sym: str, raw: dict, px: dict, tc: TipoCambio, hist_edgar: dict | None, cfg: dict) -> dict:
    info = raw.get("info") or {}
    alertas: list[str] = []
    c_l = cfg["lynch"]

    precio = px.get("precio") or num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    moneda = info.get("currency") or "USD"
    moneda_bal = info.get("financialCurrency") or moneda
    fx_px = tc.a_usd(moneda)
    fx_bal = tc.a_usd(moneda_bal)
    if fx_bal is None:
        alertas.append(f"Sin tipo de cambio para {moneda_bal}: ratios en moneda original")
        fx_bal = fx_px if moneda_bal == moneda else None

    mcap = num(info.get("marketCap"))
    # Sincroniza la capitalización con el precio de cierre usado
    p_info = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    if mcap and precio and p_info:
        mcap = mcap * precio / p_info
    mcap_usd = mcap * fx_px if mcap and fx_px else None

    m: dict = {
        "moneda": moneda, "moneda_balance": moneda_bal, "precio": precio,
        "market_cap_usd": mcap_usd,
        "sector": info.get("sector"), "industria": info.get("industry"),
    }

    # ── Dividendos (por acción operada, en moneda de cotización) ──
    div = raw.get("dividendos")
    rend_div, racha = 0.0, 0
    if div is not None and len(div):
        div = div.copy()
        idx = pd.to_datetime(div.index)
        div.index = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
        hoy = pd.Timestamp(datetime.now(timezone.utc).date())
        ult_12m = float(div[div.index > hoy - pd.Timedelta(days=365)].sum())
        rend_div = ult_12m / precio * 100 if precio else 0.0
        anios_con_div = set(div[div > 0].index.year)
        anio = hoy.year if hoy.year in anios_con_div else hoy.year - 1
        while anio in anios_con_div:
            racha += 1
            anio -= 1
    m["rend_div"] = rend_div
    m["racha_dividendos"] = racha

    if info.get("quoteType") == "ETF":
        m["tipo"] = "ETF"
        m["per"] = num(info.get("trailingPE"))
        m["rend_div"] = rend_div
        return {**m, "alertas": alertas}
    m["tipo"] = "ACCION"

    res, bal, btr = raw.get("resultados"), raw.get("balance"), raw.get("balance_trim")

    def ult_bal(claves):
        v = ultimo(serie(btr, claves))
        return v if v is not None else ultimo(serie(bal, claves))

    hay_balance = not ((btr is None or btr.empty) and (bal is None or bal.empty))

    # ── Resultados TTM (moneda del balance) ──
    ventas = num(info.get("totalRevenue")) or ultimo(serie(res, ["TotalRevenue", "OperatingRevenue"]))
    gan_ttm = num(info.get("netIncomeToCommon"))
    if gan_ttm is None:
        gan_ttm = ultimo(serie(res, ["NetIncomeCommonStockholders", "NetIncome"]))
    ebitda = num(info.get("ebitda")) or ultimo(serie(res, ["EBITDA", "NormalizedEBITDA"]))

    # ── Balance ──
    activo_corr = ult_bal(["CurrentAssets"])
    pasivo_corr = ult_bal(["CurrentLiabilities"])
    deuda_lp = ult_bal(["LongTermDebt", "LongTermDebtAndCapitalLeaseObligation"])
    deuda_tot = ult_bal(["TotalDebt"])
    if deuda_tot is None:
        deuda_tot = num(info.get("totalDebt"))
    if hay_balance:  # Yahoo omite las filas en cero
        deuda_lp = 0.0 if deuda_lp is None else deuda_lp
        deuda_tot = 0.0 if deuda_tot is None else deuda_tot
    patrimonio = ult_bal(["StockholdersEquity", "CommonStockEquity"])
    activos = ult_bal(["TotalAssets"])
    intang = ult_bal(["GoodwillAndOtherIntangibleAssets"])
    if intang is None:
        intang = (ult_bal(["Goodwill"]) or 0.0) + (ult_bal(["OtherIntangibleAssets"]) or 0.0)
    caja = ult_bal(["CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalents"])
    if caja is None:
        caja = num(info.get("totalCash"))

    def usd(v):
        return v * fx_bal if v is not None and fx_bal else None

    es_financiera = (info.get("sector") == "Financial Services") and activo_corr is None
    m["es_financiera"] = es_financiera
    m["ventas_usd"] = usd(ventas)

    # ── PER: capitalización / ganancia (consistente en ADRs) ──
    per_yahoo = num(info.get("trailingPE"))
    per_calc = mcap_usd / usd(gan_ttm) if mcap_usd and gan_ttm and gan_ttm > 0 and fx_bal else None
    if moneda == moneda_bal and per_yahoo and per_yahoo > 0:
        per = per_yahoo
    else:
        per = per_calc
    if per_yahoo and per_calc and per_yahoo > 0 and abs(per_calc / per_yahoo - 1) > 0.35:
        alertas.append(f"PER Yahoo ({per_yahoo:.1f}) y PER por capitalización ({per_calc:.1f}) difieren: revisar")
    m["per"] = per
    m["ganancia_ttm_positiva"] = gan_ttm is not None and gan_ttm > 0
    m["per_forward"] = num(info.get("forwardPE")) if moneda == moneda_bal else None

    m["pb"] = mcap_usd / usd(patrimonio) if mcap_usd and patrimonio and patrimonio > 0 and fx_bal else None
    tangible = patrimonio - intang if patrimonio is not None else None
    m["ptbv"] = mcap_usd / usd(tangible) if mcap_usd and tangible and tangible > 0 and fx_bal else None
    m["liquidez_corriente"] = activo_corr / pasivo_corr if activo_corr and pasivo_corr else None
    capital_trabajo = activo_corr - pasivo_corr if activo_corr is not None and pasivo_corr is not None else None
    m["capital_trabajo_usd"] = usd(capital_trabajo)
    m["deuda_lp_usd"] = usd(deuda_lp)
    m["deuda_lp_sobre_ct"] = (deuda_lp / capital_trabajo) if capital_trabajo and capital_trabajo > 0 and deuda_lp is not None else None
    m["patrimonio_negativo"] = patrimonio is not None and patrimonio <= 0
    m["deuda_patrimonio"] = deuda_tot / patrimonio if patrimonio and patrimonio > 0 and deuda_tot is not None else None
    m["deuda_neta_ebitda"] = (deuda_tot - (caja or 0)) / ebitda if ebitda and ebitda > 0 and deuda_tot is not None else None
    m["caja_neta_pct"] = usd((caja or 0) - deuda_tot) / mcap_usd if mcap_usd and deuda_tot is not None and fx_bal else None
    m["patrimonio_activos"] = patrimonio / activos if patrimonio and activos else None
    m["roe"] = num(info.get("returnOnEquity"))
    m["margen_neto"] = num(info.get("profitMargins"))
    m["inst_pct"] = num(info.get("heldPercentInstitutions"))
    m["payout"] = num(info.get("payoutRatio"))

    # ── Inventarios vs ventas (alerta de Lynch) ──
    inv = serie(bal, ["Inventory"])
    vtas = serie(res, ["TotalRevenue", "OperatingRevenue"])
    m["inventario_brecha_pp"] = None
    # Solo si los inventarios pesan (ej. ≥ 5% de las ventas); en Alphabet o Visa son irrelevantes
    material = len(inv) and len(vtas) and vtas.iloc[-1] > 0 and inv.iloc[-1] / vtas.iloc[-1] >= c_l.get("inventario_min_ventas", 0.05)
    if material and len(inv) >= 2 and len(vtas) >= 2 and inv.iloc[-2] > 0 and vtas.iloc[-2] > 0:
        g_inv = inv.iloc[-1] / inv.iloc[-2] - 1
        g_vtas = vtas.iloc[-1] / vtas.iloc[-2] - 1
        m["inventario_crec"] = g_inv * 100
        m["ventas_crec"] = g_vtas * 100
        m["inventario_brecha_pp"] = (g_inv - g_vtas) * 100

    # ── Historia anual de EPS y ganancia neta ──
    yf_eps = serie(res, ["DilutedEPS", "BasicEPS"])
    yf_gan = serie(res, ["NetIncomeCommonStockholders", "NetIncome"])
    yf_eps = ajustar_splits_yahoo(yf_eps, yf_gan, raw.get("splits"))
    fuente, eps, gan, moneda_hist = "Yahoo Finance", yf_eps, yf_gan, moneda_bal
    m["edgar"] = None
    if hist_edgar:
        e_eps, q_eps = cortar_quiebres(hist_edgar["eps"])
        e_gan, q_gan = cortar_quiebres(hist_edgar["ganancia"])
        if q_eps or q_gan:
            alertas.append("La serie de la SEC cambia de base (moneda o cantidad de acciones) en años viejos: "
                           "se descartaron esos años")

        def al_dia(s: pd.Series, ref: pd.Series) -> bool:
            return len(s) > 0 and (len(ref) == 0 or s.index[-1] >= ref.index[-1] - pd.Timedelta(days=120))

        # La SEC a veces no tiene cargado el último 20-F/10-K: se completa con Yahoo
        eps_emp, n_eps = empalmar(e_eps, yf_eps)
        gan_emp, n_gan = empalmar(e_gan, yf_gan)
        m["edgar"] = {"cik": hist_edgar.get("cik"), "moneda": hist_edgar.get("moneda"),
                      "eps_anios": len(e_eps), "ganancia_anios": len(e_gan),
                      "ultimo": e_eps.index[-1].strftime("%Y-%m-%d") if len(e_eps) else None,
                      "anios_agregados_yahoo": n_eps}
        if len(e_eps) >= 5 and al_dia(eps_emp, yf_eps):
            fuente = "SEC EDGAR" + (f" + Yahoo ({n_eps} último{'s' if n_eps > 1 else ''})" if n_eps else "")
            eps = eps_emp
            gan = gan_emp if len(gan_emp) >= 3 and al_dia(gan_emp, yf_gan) else yf_gan
            moneda_hist = hist_edgar.get("moneda") or moneda_bal
        elif len(e_gan) >= 6 and al_dia(gan_emp, yf_gan):
            # Sin EPS sin dimensiones en XBRL (ej. varias clases de acciones): se usa la
            # ganancia neta como aproximación. Subestima el crecimiento por acción si hay recompras.
            fuente, eps, gan = "SEC EDGAR (ganancia neta)", gan_emp, gan_emp
            moneda_hist = hist_edgar.get("moneda") or moneda_bal
            alertas.append("Historia de 10 años con ganancia neta total (no por acción): ignora el efecto de recompras")
    m["fuente_historia"] = fuente

    # En monedas de alta inflación, el crecimiento nominal no sirve: se pasa a USD por año
    def a_usd_hist(s: pd.Series) -> pd.Series:
        if moneda_hist in ALTA_INFLACION:
            vals = {t: v * (tc.a_usd(moneda_hist, t) or float("nan")) for t, v in s.items()}
            return pd.Series(vals, dtype=float).dropna()
        f = tc.a_usd(moneda_hist) or fx_bal
        return s * f if f else s

    if moneda_hist in ALTA_INFLACION:
        alertas.append(f"Balances en {moneda_hist}: crecimiento medido en USD (tipo de cambio de cada cierre), aproximado")
        eps_g = a_usd_hist(eps)
    else:
        eps_g = eps  # el crecimiento en moneda local es el económicamente relevante
    gan_usd = a_usd_hist(gan)

    eps = eps[eps.index >= eps.index.max() - pd.DateOffset(years=11)] if len(eps) else eps
    eps_g = eps_g.loc[eps_g.index.isin(eps.index)] if len(eps_g) else eps_g
    m["eps_hist"] = a_lista(eps)
    m["moneda_hist"] = moneda_hist
    vals = [float(v) for v in eps.values]
    m["eps_anios"] = len(vals)

    # Estabilidad: ganancias positivas (EPS; si no hay, ganancia neta)
    base_estab = vals if len(vals) >= len(gan) else [float(v) for v in gan.values]
    ult10 = base_estab[-cfg["graham"]["anios_ganancias"]:]
    m["estab_anios"] = len(ult10)
    m["estab_positivas"] = all(v > 0 for v in ult10) if ult10 else None
    m["perdida_ultimos_2"] = any(v <= 0 for v in base_estab[-2:]) if base_estab else None
    n5 = cfg["graham"]["emp_anios_sin_perdidas"]
    ult5 = base_estab[-n5:]
    m["sin_perdidas_5a"] = False if any(v <= 0 for v in ult5) else (True if len(ult5) >= n5 else None)
    ventana = base_estab[-c_l["ventana_ciclo_anios"]:]
    m["perdida_en_ciclo"] = any(v <= 0 for v in ventana) if ventana else None
    m["caida_max_eps"] = caida_maxima(base_estab[-c_l["ventana_ciclo_anios"]:]) if len(base_estab) >= 2 else None

    # Crecimiento de EPS a 10 años (Graham): promedios de 3 años al inicio y al final
    ev = [float(v) for v in eps_g.values][-10:]
    m["crec_10a"], m["crec_10a_anios"] = None, len(ev)
    k = 3 if len(ev) >= 6 else 2 if len(ev) >= 4 else 0
    if k:
        ini, fin = sum(ev[:k]) / k, sum(ev[-k:]) / k
        if ini > 0:
            m["crec_10a"] = (fin / ini - 1) * 100
    # Emprendedor: último EPS > el de ~5 años antes
    m["eps_vs_5a"] = None
    if len(eps_g) >= 4:
        fin_t = eps_g.index[-1]
        previos = eps_g[eps_g.index <= fin_t - pd.DateOffset(years=4)]
        if len(previos):
            m["eps_vs_5a"] = float(eps_g.iloc[-1]) > float(previos.iloc[-1])

    # ── Crecimiento esperado: mediana de histórico, estimación 1-2 años y largo plazo ──
    g_hist, g_hist_anios = crecimiento_historico(eps_g)
    g_fwd = None
    ee = raw.get("est_ganancias")
    if ee is not None and not ee.empty and "growth" in ee.columns and moneda_hist not in ALTA_INFLACION:
        # Se prefiere el año próximo vs. el actual (+1y): compara dos estimaciones "limpias".
        # El año en curso (0y) se compara contra un balance real que puede tener extraordinarios.
        for p in ("+1y", "0y"):
            if p in ee.index and num(ee.loc[p, "growth"]) is not None:
                g_fwd = num(ee.loc[p, "growth"]) * 100
                break
    g_ltg = None
    ge = raw.get("est_crecimiento")
    if ge is not None and not ge.empty and "stockTrend" in ge.columns:
        for p in ("+5y", "LTG"):
            if p in ge.index and num(ge.loc[p, "stockTrend"]) is not None:
                g_ltg = num(ge.loc[p, "stockTrend"]) * 100
                break
    peg_yahoo = num(info.get("trailingPegRatio"))
    if g_ltg is None and peg_yahoo and peg_yahoo > 0 and per_yahoo and per_yahoo > 0 and moneda == moneda_bal:
        g_ltg = per_yahoo / peg_yahoo
    candidatos = [g for g in (g_hist, g_fwd, g_ltg) if g is not None]
    g_final = statistics.median(candidatos) if candidatos else None
    if len(candidatos) >= 2 and max(candidatos) - min(candidatos) > 25:
        partes = [f"{n} {v:.0f}%".replace(".", ",") for n, v in
                  (("histórico", g_hist), ("analistas próximo año", g_fwd), ("largo plazo", g_ltg)) if v is not None]
        alertas.append("Estimaciones de crecimiento muy dispares (" + " · ".join(partes) +
                       "): conviene revisar la categoría de Lynch a mano")
    m["crecimiento"] = {"final": g_final, "historico": g_hist, "historico_anios": g_hist_anios,
                        "estimado_1_2a": g_fwd, "largo_plazo": g_ltg}
    g_lynch = min(g_final, c_l["crecimiento_max"]) if g_final is not None else None
    m["crec_lynch"] = g_lynch

    # ── Ratios de Lynch ──
    ajuste_caja = min(max(m["caja_neta_pct"] or 0.0, 0.0), 0.8)
    m["per_eff"] = per * (1 - ajuste_caja) if per else None
    m["peg"] = m["per_eff"] / g_lynch if m["per_eff"] and g_lynch and g_lynch > 0 else None
    m["div_ajustado"] = (g_lynch + rend_div) / per if per and g_lynch is not None else None

    # ── Ratios de Graham sobre promedios ──
    g3 = list(gan_usd.values)[-3:]
    prom3 = sum(g3) / len(g3) if len(g3) == 3 else None
    m["per_3a"] = mcap_usd / prom3 if mcap_usd and prom3 and prom3 > 0 else None
    gciclo = list(gan_usd.values)[-c_l["ventana_ciclo_anios"]:]
    prom_ciclo = sum(gciclo) / len(gciclo) if len(gciclo) >= 4 else None
    m["per_normalizado"] = mcap_usd / prom_ciclo if mcap_usd and prom_ciclo and prom_ciclo > 0 else None
    gan_ttm_usd = usd(gan_ttm)
    m["ciclo_ratio"] = gan_ttm_usd / prom_ciclo if gan_ttm_usd is not None and prom_ciclo and prom_ciclo > 0 else None
    m["ciclo_anios"] = len(gciclo)

    return {**m, "alertas": alertas}
