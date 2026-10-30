"""Tests de las reglas con casos armados a mano (sin red)."""
from pathlib import Path

import pandas as pd
import yaml

from radar import graham, lynch
from radar.edgar import ajustar_por_splits
from radar.metricas import ajustar_splits_yahoo, construir, crecimiento_historico
from radar.reglas_comunes import combinar

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))


def base(**kw):
    m = {
        "tipo": "ACCION", "industria": "Software - Application", "sector": "Technology",
        "ganancia_ttm_positiva": True, "perdida_ultimos_2": False, "perdida_en_ciclo": False, "caida_max_eps": 0.05,
        "crecimiento": {"final": 20.0, "historico": 20.0}, "crec_lynch": 20.0,
        "per": 20.0, "per_eff": 20.0, "peg": 1.0, "div_ajustado": 1.0, "rend_div": 0.0,
        "deuda_patrimonio": 0.3, "patrimonio_negativo": False, "es_financiera": False,
        "caja_neta_pct": 0.05, "inst_pct": 0.7, "inventario_brecha_pp": None,
        "ventas_usd": 10e9, "liquidez_corriente": 2.5, "deuda_lp_sobre_ct": 0.5, "capital_trabajo_usd": 2e9,
        "deuda_lp_usd": 1e9, "estab_anios": 10, "estab_positivas": True, "racha_dividendos": 25,
        "crec_10a": 80.0, "crec_10a_anios": 10, "per_3a": 13.0, "pb": 1.4, "ptbv": 2.0,
        "sin_perdidas_5a": True, "eps_vs_5a": True,
    }
    m.update(kw)
    return m


# ── Lynch ──
def test_rapido_peg_bajo_compra():
    ev = lynch.evaluar(base(crecimiento={"final": 25.0}, crec_lynch=25.0, per_eff=20, peg=0.8), CFG)
    assert ev["categoria"] == "rapido" and ev["senal"] == "COMPRAR"


def test_rapido_peg_alto_vende():
    ev = lynch.evaluar(base(crecimiento={"final": 20.0}, crec_lynch=20.0, per=60, per_eff=60, peg=3.0), CFG)
    assert ev["senal"] == "VENDER"


def test_compra_bloqueada_por_deuda():
    ev = lynch.evaluar(base(peg=0.7, deuda_patrimonio=2.5), CFG)
    assert ev["senal"] == "MANTENER" and "endeudamiento" in ev["motivo"]


def test_lento_con_dividendo_alto_compra():
    # crecimiento 4% + dividendo 6% sobre PER 5 → 2,0
    ev = lynch.evaluar(base(crecimiento={"final": 4.0}, crec_lynch=4.0, per=5, per_eff=5, peg=1.25, div_ajustado=2.0, rend_div=6.0), CFG)
    assert ev["categoria"] == "lento" and ev["senal"] == "COMPRAR"


def test_estable_caro_vende():
    ev = lynch.evaluar(base(crecimiento={"final": 8.0}, crec_lynch=8.0, per=25, per_eff=25, peg=3.1, div_ajustado=0.45), CFG)
    assert ev["categoria"] == "estable" and ev["senal"] == "VENDER"


def test_turnaround_con_perdidas_y_deuda_vende():
    ev = lynch.evaluar(base(ganancia_ttm_positiva=False, per=None, caja_neta_pct=-0.3), CFG)
    assert ev["categoria"] == "recuperacion" and ev["senal"] == "VENDER"


def test_turnaround_con_caja_mantiene():
    ev = lynch.evaluar(base(ganancia_ttm_positiva=False, per=None, caja_neta_pct=0.2), CFG)
    assert ev["senal"] == "MANTENER"


def test_ciclica_en_pico_no_compra():
    m = base(industria="Semiconductors", perdida_en_ciclo=True, per_normalizado=10, ciclo_ratio=2.0, ciclo_anios=7)
    ev = lynch.evaluar(m, CFG)
    assert ev["categoria"] == "ciclica" and ev["senal"] == "MANTENER"


def test_ciclica_cara_vende():
    m = base(industria="Semiconductors", perdida_en_ciclo=True, per_normalizado=40, ciclo_ratio=2.5, ciclo_anios=7)
    assert lynch.evaluar(m, CFG)["senal"] == "VENDER"


def test_semis_crecimiento_secular_no_es_ciclica():
    m = base(industria="Semiconductors", perdida_en_ciclo=False, caida_max_eps=0.55,
             crecimiento={"final": 45.0, "historico": 80.0}, crec_lynch=45.0)
    assert lynch.evaluar(m, CFG)["categoria"] == "rapido"


def test_override_categoria():
    assert lynch.evaluar(base(), CFG, "ciclica")["categoria"] == "ciclica"


def test_banco_usa_patrimonio_sobre_activos():
    ev = lynch.evaluar(base(es_financiera=True, deuda_patrimonio=8.0, patrimonio_activos=0.09, peg=0.8), CFG)
    assert ev["senal"] == "COMPRAR"


# ── Graham ──
def test_graham_defensivo_compra():
    ev = graham.evaluar(base(), CFG, 5.3)
    assert ev["senal"] == "COMPRAR", ev["motivo"]
    assert ev["defensivo"]["cumple"] == 8  # 7 criterios, el 2 tiene dos partes


def test_graham_sin_dividendos_no_compra_defensivo():
    ev = graham.evaluar(base(racha_dividendos=3, rend_div=0.0, ptbv=2.0), CFG, 5.3)
    assert ev["senal"] != "COMPRAR"


def test_graham_caro_vende():
    ev = graham.evaluar(base(per=60, per_3a=70, pb=15, crec_lynch=20), CFG, 5.3)
    assert ev["senal"] == "VENDER"
    # g tope 15 → PER justo = (8,5 + 30) × 4,4 / 5,3 = 31,96
    assert abs(ev["valuacion"]["per_justo_formula"] - 31.96) < 0.01


def test_graham_sin_ganancias_vende():
    assert graham.evaluar(base(ganancia_ttm_positiva=False, per=None), CFG, 5.3)["senal"] == "VENDER"


def test_graham_emprendedor_net_net():
    m = base(racha_dividendos=2, rend_div=1.0, per_3a=30, pb=0.9, ptbv=1.0, liquidez_corriente=1.8, deuda_lp_sobre_ct=0.8)
    ev = graham.evaluar(m, CFG, 5.3)
    assert ev["senal"] == "COMPRAR" and "emprendedor" in ev["motivo"]


def test_numero_graham():
    ev = graham.evaluar(base(per=15, pb=1.5), CFG, 5.3)
    assert abs(ev["valuacion"]["precio_sobre_numero_graham"] - 1.0) < 1e-9


# ── Combinada ──
def test_combinar():
    assert combinar("COMPRAR", "MANTENER")[0] == "COMPRAR"
    assert combinar("COMPRAR", "VENDER")[0] == "MANTENER"
    assert combinar("MANTENER", "VENDER")[0] == "VENDER"
    assert combinar("VENDER", "VENDER")[0] == "VENDER"


# ── Datos ──
def test_splits_edgar_por_fecha_de_presentacion():
    from datetime import date
    eps = [{"fin": date(2019, 1, 27), "valor": 6.63, "presentado": date(2019, 2, 21)},
           {"fin": date(2024, 1, 28), "valor": 1.19, "presentado": date(2025, 2, 26)}]
    splits = pd.Series({pd.Timestamp("2021-07-20"): 4.0, pd.Timestamp("2024-06-10"): 10.0})
    out = ajustar_por_splits(eps, splits)
    assert abs(out[0]["valor"] - 6.63 / 40) < 1e-9   # presentado antes de ambos splits
    assert out[1]["valor"] == 1.19                    # ya re-expresado


def test_splits_yahoo_detecta_salto():
    idx = pd.to_datetime(["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"])
    gan = pd.Series([100.0, 110, 120, 130], index=idx)
    eps = pd.Series([10.0, 11.0, 1.2, 1.3], index=idx)      # sin ajustar: split 10:1 en 2023
    splits = pd.Series({pd.Timestamp("2023-06-01"): 10.0})
    out = ajustar_splits_yahoo(eps, gan, splits)
    assert abs(out.iloc[0] - 1.0) < 1e-9 and abs(out.iloc[1] - 1.1) < 1e-9
    eps_ok = pd.Series([1.0, 1.1, 1.2, 1.3], index=idx)     # ya ajustado: no tocar
    assert list(ajustar_splits_yahoo(eps_ok, gan, splits)) == [1.0, 1.1, 1.2, 1.3]


def test_crecimiento_historico():
    idx = pd.to_datetime([f"{y}-12-31" for y in range(2015, 2026)])
    eps = pd.Series([1.0 * 1.1 ** i for i in range(11)], index=idx)
    g, anios = crecimiento_historico(eps)
    assert abs(g - 10.0) < 0.01 and anios == 5


class TCFalso:
    def a_usd(self, moneda, fecha=None):
        return {"USD": 1.0, "TWD": 1 / 32, "ARS": 1 / 1400}.get(moneda or "USD")


def raw_falso(moneda_bal="USD", ratio_adr=1.0):
    idx = pd.to_datetime([f"{y}-12-31" for y in range(2021, 2026)])
    res = pd.DataFrame({t: {"TotalRevenue": 1000.0 * (1.1 ** i), "NetIncomeCommonStockholders": 100.0 * (1.1 ** i),
                            "DilutedEPS": 1.0 * (1.1 ** i)} for i, t in enumerate(idx)})
    bal = pd.DataFrame({t: {"CurrentAssets": 600.0, "CurrentLiabilities": 250.0, "LongTermDebt": 200.0, "TotalDebt": 250.0,
                            "StockholdersEquity": 800.0, "TotalAssets": 1500.0, "CashAndCashEquivalents": 150.0,
                            "Inventory": 100.0 * (1.05 ** i)} for i, t in enumerate(idx)})
    fx = TCFalso().a_usd(moneda_bal)
    info = {"quoteType": "EQUITY", "currency": "USD", "financialCurrency": moneda_bal, "currentPrice": 20.0,
            "marketCap": 2000.0 * fx * 1.0, "totalRevenue": 1500.0, "netIncomeToCommon": 150.0, "ebitda": 300.0,
            "trailingPE": 13.33, "sector": "Industrials", "industry": "Specialty Industrial Machinery"}
    return {"info": info, "resultados": res, "balance": bal, "balance_trim": pd.DataFrame(), "dividendos": None,
            "splits": None, "est_ganancias": pd.DataFrame(), "est_crecimiento": pd.DataFrame()}


def test_construir_adr_en_otra_moneda():
    # Balance en TWD, cotización en USD: el PER debe salir de capitalización / ganancia convertida
    raw = raw_falso("TWD")
    m = construir("XYZ", raw, {"precio": 20.0}, TCFalso(), None, CFG)
    assert abs(m["per"] - 2000 / 32 / (150 / 32)) < 1e-6          # = 13,33
    assert abs(m["pb"] - (2000 / 32) / (800 / 32)) < 1e-6         # = 2,5
    assert abs(m["liquidez_corriente"] - 2.4) < 1e-9
    assert abs(m["crecimiento"]["historico"] - 10.0) < 0.01


def test_construir_ars_convierte_a_usd():
    raw = raw_falso("ARS")
    m = construir("XYZ", raw, {"precio": 20.0}, TCFalso(), None, CFG)
    assert any("ARS" in a for a in m["alertas"])
