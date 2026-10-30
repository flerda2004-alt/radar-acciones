"""Criterios de Benjamin Graham, "El inversor inteligente" (ed. revisada 1973).

Cap. 14 — Inversor defensivo (7 criterios):
  1. Tamaño adecuado          5. Crecimiento de EPS ≥ 1/3 en 10 años (promedios de 3 años)
  2. Situación financiera     6. PER ≤ 15 sobre la ganancia promedio de 3 años
     (liquidez ≥ 2; deuda LP ≤ capital de trabajo)
  3. Ganancias 10 años        7. P/VL ≤ 1,5, o PER × P/VL ≤ 22,5
  4. Dividendos 20 años
Cap. 15 — Inversor emprendedor: liquidez ≥ 1,5; deuda ≤ 110% del capital de trabajo;
  sin pérdidas en 5 años; algún dividendo; EPS actual > EPS de 5 años atrás;
  precio < 120% del valor contable tangible.
Valor intrínseco (cap. 11): V = EPS × (8,5 + 2g) × 4,4 / Y   (Y = rendimiento AAA)
Número de Graham: √(22,5 × EPS × VL por acción)  ⇔  precio ≤ NG cuando PER × P/VL ≤ 22,5
"""
from __future__ import annotations

import math

from .reglas_comunes import COMPRAR, MANTENER, VENDER, _fmt, check, f1, f2, fn, pct


def evaluar(m: dict, cfg: dict, tasa_aaa: float) -> dict:
    c = cfg["graham"]
    fin = m.get("es_financiera")
    per, pb, per3 = m.get("per"), m.get("pb"), m.get("per_3a")

    # ── Inversor defensivo ──
    ventas = m.get("ventas_usd")
    d = []
    d.append(check("1. Tamaño: ventas anuales", None if ventas is None else ventas >= c["ventas_minimas_usd_mm"] * 1e6,
                   f"USD {_fmt(ventas / 1e6, 0) if ventas else 's/d'} M", f"≥ USD {_fmt(c['ventas_minimas_usd_mm'], 0)} M"))
    if fin:
        d.append(check("2a. Liquidez corriente", None, "n/a", f"≥ {fn(c['liquidez_corriente_min'])}", "No aplica a bancos/aseguradoras"))
        d[-1]["estado"] = "n/a"
        d.append(check("2b. Deuda LP ≤ capital de trabajo", None, "n/a", "≤ 1", "No aplica a bancos/aseguradoras"))
        d[-1]["estado"] = "n/a"
    else:
        lc = m.get("liquidez_corriente")
        d.append(check("2a. Liquidez corriente", None if lc is None else lc >= c["liquidez_corriente_min"], f2(lc),
                       f"≥ {fn(c['liquidez_corriente_min'])}"))
        dlp_ct = m.get("deuda_lp_sobre_ct")
        ct = m.get("capital_trabajo_usd")
        if ct is not None and ct <= 0:
            ok = (m.get("deuda_lp_usd") or 0) == 0
            d.append(check("2b. Deuda LP ≤ capital de trabajo", ok, "capital de trabajo negativo", "≤ 1"))
        else:
            d.append(check("2b. Deuda LP ≤ capital de trabajo", None if dlp_ct is None else dlp_ct <= 1, f2(dlp_ct), "≤ 1"))
    anios = m.get("estab_anios") or 0
    nota_anios = "" if anios >= c["anios_ganancias"] else f"Solo {anios} ejercicios disponibles de {fn(c['anios_ganancias'])}"
    d.append(check(f"3. Ganancias positivas {fn(c['anios_ganancias'])} años", m.get("estab_positivas"),
                   f"{anios} años" + (" positivos" if m.get("estab_positivas") else " con pérdidas" if m.get("estab_positivas") is False else ""),
                   "todos positivos", nota_anios))
    racha = m.get("racha_dividendos") or 0
    d.append(check(f"4. Dividendos ininterrumpidos {fn(c['anios_dividendos'])} años", racha >= c["anios_dividendos"],
                   f"{racha} años", f"≥ {fn(c['anios_dividendos'])}"))
    cr10 = m.get("crec_10a")
    n10 = m.get("crec_10a_anios") or 0
    d.append(check("5. Crecimiento de EPS en 10 años", None if cr10 is None else cr10 >= c["crecimiento_eps_10a_min"],
                   pct(cr10), f"≥ {c['crecimiento_eps_10a_min']}%",
                   "" if n10 >= 10 else f"Calculado con {n10} ejercicios"))
    d.append(check("6. PER sobre ganancia promedio 3 años", None if per3 is None else per3 <= c["per_max"], f1(per3),
                   f"≤ {fn(c['per_max'])}"))
    combo = per3 * pb if per3 and pb else None
    ok7 = None
    if pb is not None:
        ok7 = pb <= c["pb_max"] or (combo is not None and combo <= c["per_x_pb_max"])
    d.append(check("7. P/VL o PER × P/VL", ok7, f"P/VL {f2(pb)} · PER×P/VL {f1(combo)}",
                   f"P/VL ≤ {fn(c['pb_max'])} o producto ≤ {fn(c['per_x_pb_max'])}"))

    calidad = d[:5]
    fallas_calidad = sum(1 for x in calidad if x["estado"] in ("no cumple", "sin datos"))
    precio_ok = d[5]["estado"] == "cumple" and d[6]["estado"] == "cumple"
    aplicables = [x for x in d if x["estado"] != "n/a"]
    cumple_def = sum(1 for x in aplicables if x["estado"] == "cumple")

    # ── Inversor emprendedor ──
    e = []
    if fin:
        for nombre in ("E1. Liquidez corriente", "E2. Deuda ≤ 110% del capital de trabajo"):
            e.append({**check(nombre, None, "n/a", "", "No aplica a bancos/aseguradoras"), "estado": "n/a"})
    else:
        lc = m.get("liquidez_corriente")
        e.append(check("E1. Liquidez corriente", None if lc is None else lc >= c["emp_liquidez_min"], f2(lc), f"≥ {fn(c['emp_liquidez_min'])}"))
        dlp_ct = m.get("deuda_lp_sobre_ct")
        ct = m.get("capital_trabajo_usd")
        ok_e2 = ((m.get("deuda_lp_usd") or 0) == 0) if (ct is not None and ct <= 0) else (None if dlp_ct is None else dlp_ct <= c["emp_deuda_vs_capital_trabajo"])
        e.append(check("E2. Deuda ≤ 110% del capital de trabajo", ok_e2, f2(dlp_ct) if dlp_ct is not None else "s/d",
                       f"≤ {fn(c['emp_deuda_vs_capital_trabajo'])}"))
    e.append(check(f"E3. Sin pérdidas en {fn(c['emp_anios_sin_perdidas'])} años", m.get("sin_perdidas_5a"), "", "sin pérdidas"))
    e.append(check("E4. Paga dividendo actualmente", (m.get("rend_div") or 0) > 0, pct(m.get("rend_div")), "> 0"))
    e.append(check("E5. EPS actual > EPS de hace ~5 años", m.get("eps_vs_5a"), "", "creciente"))
    ptbv = m.get("ptbv")
    e.append(check("E6. Precio / valor contable tangible", None if ptbv is None else ptbv <= c["emp_ptbv_max"], f2(ptbv),
                   f"≤ {fn(c['emp_ptbv_max'])}"))
    emp_calidad_ok = all(x["estado"] in ("cumple", "n/a") for x in e[:5])
    emp_ok = emp_calidad_ok and e[5]["estado"] == "cumple"

    # ── Valuación ──
    g_raw = m.get("crec_lynch")
    g = max(0.0, min(g_raw, c["crecimiento_formula_tope"])) if g_raw is not None else 0.0
    mult_v = (8.5 + 2 * g) * 4.4 / tasa_aaa
    p_sobre_v = per / mult_v if per else None
    ng_ratio = math.sqrt(per * pb / 22.5) if per and pb else None
    valuacion = {
        "precio_sobre_valor_intrinseco": p_sobre_v,
        "margen_seguridad": (1 - p_sobre_v) if p_sobre_v else None,
        "precio_sobre_numero_graham": ng_ratio,
        "per_justo_formula": mult_v,
        "g_usado": g, "tasa_aaa": tasa_aaa,
    }

    # ── Señal ──
    if not m.get("ganancia_ttm_positiva") or not per:
        senal, motivo = VENDER, "Sin ganancias en los últimos 12 meses: fuera del universo de Graham"
    elif precio_ok and fallas_calidad <= c["fallas_toleradas"]:
        senal, motivo = COMPRAR, f"Cumple los criterios del inversor defensivo ({cumple_def}/{len(aplicables)})"
    elif emp_ok:
        senal, motivo = COMPRAR, "Cumple los criterios del inversor emprendedor (incl. precio < 120% del valor tangible)"
    elif p_sobre_v is not None and p_sobre_v > 1 and not precio_ok:
        senal = VENDER
        motivo = (f"PER {f1(per)} > PER justo de la fórmula ({f1(mult_v)}, g={pct(g)}, AAA={pct(tasa_aaa)}) "
                  f"y no cumple los límites de precio")
    else:
        motivo = f"Margen de seguridad {pct((1 - p_sobre_v) * 100) if p_sobre_v else 's/d'} según la fórmula; "
        motivo += "precio dentro de límites pero falla en calidad" if precio_ok else "no cumple PER ≤ 15 / P/VL"
        senal = MANTENER

    return {"senal": senal, "motivo": motivo,
            "defensivo": {"checks": d, "cumple": cumple_def, "aplicables": len(aplicables)},
            "emprendedor": {"checks": e, "cumple": sum(1 for x in e if x["estado"] == "cumple"),
                            "aplicables": sum(1 for x in e if x["estado"] != "n/a")},
            "valuacion": valuacion}
