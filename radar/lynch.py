"""Criterios de Peter Lynch ("One Up on Wall Street", "Beating the Street").

1. Clasifica la empresa en una de sus seis categorías (la valuación depende de la categoría).
2. Valúa: PEG (PER / crecimiento), PEG ajustado por dividendos, PER normalizado para cíclicas.
3. Controla el balance: deuda/patrimonio, caja neta, inventarios vs. ventas.
"""
from __future__ import annotations

from .reglas_comunes import COMPRAR, MANTENER, VENDER, check, f1, f2, fn, pct

CATEGORIAS = {
    "rapido": "Crecimiento rápido",
    "estable": "Estable (stalwart)",
    "lento": "Crecimiento lento",
    "ciclica": "Cíclica",
    "recuperacion": "Recuperación",
    "sin_datos": "Sin datos suficientes",
}


def clasificar(m: dict, c: dict, override: str | None = None) -> tuple[str, str]:
    if override in CATEGORIAS:
        return override, "Categoría fijada manualmente en watchlist.yaml"
    if not m.get("ganancia_ttm_positiva") or m.get("perdida_ultimos_2"):
        return "recuperacion", "Pérdidas en los últimos 12 meses o en alguno de los 2 últimos ejercicios"
    g = m.get("crecimiento", {}).get("final")
    g_hist = m.get("crecimiento", {}).get("historico")
    if m.get("industria") in c["industrias_ciclicas"]:
        volatil = m.get("perdida_en_ciclo") or (
            (m.get("caida_max_eps") or 0) >= c["ciclica_caida_min"]
            and (g_hist is None or g_hist < c["umbral_rapido"])
        )
        if volatil:
            return "ciclica", f"Industria cíclica ({m.get('industria')}) con ganancias volátiles en el ciclo"
    if g is None:
        return "sin_datos", "No hay historia ni estimaciones de crecimiento de ganancias"
    if g >= c["umbral_rapido"]:
        return "rapido", f"Crecimiento de ganancias estimado {pct(g)} ≥ {fn(c['umbral_rapido'])}%"
    if g >= c["umbral_estable"]:
        return "estable", f"Crecimiento de ganancias estimado {pct(g)} entre {fn(c['umbral_estable'])}% y {fn(c['umbral_rapido'])}%"
    return "lento", f"Crecimiento de ganancias estimado {pct(g)} < {fn(c['umbral_estable'])}%"


def _check_deuda(m: dict, c: dict) -> dict:
    if m.get("es_financiera"):
        pa = m.get("patrimonio_activos")
        return check("Solidez (bancos): patrimonio / activos", None if pa is None else pa >= c["patrimonio_activos_min"],
                     pct(pa * 100 if pa is not None else None), f"≥ {pct(c['patrimonio_activos_min'] * 100)}")
    if m.get("patrimonio_negativo"):
        dne = m.get("deuda_neta_ebitda")
        return check("Deuda neta / EBITDA (patrimonio negativo)", None if dne is None else dne <= c["deuda_neta_ebitda_max"],
                     f2(dne), f"≤ {fn(c['deuda_neta_ebitda_max'])}",
                     "Patrimonio negativo, típico de recompras agresivas: se mide la deuda contra EBITDA")
    de = m.get("deuda_patrimonio")
    return check("Deuda / patrimonio", None if de is None else de <= c["deuda_patrimonio_max"],
                 f2(de), f"≤ {fn(c['deuda_patrimonio_max'])}")


def evaluar(m: dict, cfg: dict, override: str | None = None) -> dict:
    c = cfg["lynch"]
    cat, motivo_cat = clasificar(m, c, override)
    checks = []
    g = m.get("crec_lynch")
    g_raw = (m.get("crecimiento") or {}).get("final")
    per, per_eff, peg, da = m.get("per"), m.get("per_eff"), m.get("peg"), m.get("div_ajustado")
    alertas = []

    deuda = _check_deuda(m, c)
    checks.append(deuda)

    brecha = m.get("inventario_brecha_pp")
    inv_mal = None
    if brecha is not None:
        inv_mal = brecha > c["inventario_brecha_pp"] and (m.get("inventario_crec") or 0) > 10
        checks.append(check("Inventarios vs. ventas (último ejercicio)", not inv_mal,
                            f"inv. {pct(m.get('inventario_crec'))} / ventas {pct(m.get('ventas_crec'))}",
                            "inventarios no crecen > ventas",
                            "Lynch: inventarios que crecen más rápido que las ventas son señal de alerta"))

    cn = m.get("caja_neta_pct")
    if cn is not None:
        checks.append(check("Caja neta / capitalización", cn > 0, pct(cn * 100), "> 0%",
                            "Lynch descuenta la caja neta del precio: PER efectivo = PER × (1 − caja neta/cap.)",
                            informativo=True))
    inst = m.get("inst_pct")
    if inst is not None:
        checks.append(check("Tenencia institucional", inst < 0.4, pct(inst * 100), "< 40% (poco seguida)",
                            "Lynch prefería empresas poco cubiertas por institucionales", informativo=True))

    if g_raw is not None and g_raw > c["crecimiento_max"]:
        alertas.append(f"Crecimiento estimado {pct(g_raw)}: Lynch desconfiaba de tasas > {fn(c['crecimiento_max'])}%; "
                       f"se usa {fn(c['crecimiento_max'])}% para el PEG")

    # ── Valuación según categoría ──
    if cat in ("rapido", "estable", "lento"):
        checks.append(check("PEG (PER efectivo / crecimiento)", None if peg is None else peg <= c["peg_compra"],
                            f2(peg), f"≤ {fn(c['peg_compra'])} compra · > {fn(c['peg_venta'])} venta",
                            f"PER {f1(per)} → efectivo {f1(per_eff)} · crecimiento {pct(g)}"))
        if cat != "rapido":
            checks.append(check("(Crecimiento + dividendo) / PER", None if da is None else da >= c["dividendo_ajustado_compra"],
                                f2(da), f"≥ {fn(c['dividendo_ajustado_compra'])} compra · < {fn(c['dividendo_ajustado_venta'])} venta",
                                f"Rend. dividendo {pct(m.get('rend_div'))}"))
        if cat == "rapido":
            if peg is None:
                senal, motivo = MANTENER, "Sin PEG calculable"
            elif peg <= c["peg_compra"]:
                senal, motivo = COMPRAR, f"PEG {f2(peg)} ≤ {fn(c['peg_compra'])}: el precio no refleja el crecimiento"
            elif peg > c["peg_venta"]:
                senal, motivo = VENDER, f"PEG {f2(peg)} > {fn(c['peg_venta'])}: PER más del doble del crecimiento"
            else:
                senal, motivo = MANTENER, f"PEG {f2(peg)} entre {fn(c['peg_compra'])} y {fn(c['peg_venta'])}"
        else:
            barata = (peg is not None and peg <= c["peg_compra"]) or (da is not None and da >= c["dividendo_ajustado_compra"])
            cara = (peg is None or peg > c["peg_venta"]) and (da is None or da < c["dividendo_ajustado_venta"])
            if barata:
                senal = COMPRAR
                motivo = f"PEG {f2(peg)} / (crec.+div.)/PER {f2(da)}: valuación atractiva para su crecimiento"
            elif cara:
                senal = VENDER
                motivo = f"PEG {f2(peg)} y (crec.+div.)/PER {f2(da)}: paga demasiado por un crecimiento de {pct(g)}"
            else:
                senal, motivo = MANTENER, f"PEG {f2(peg)} · (crec.+div.)/PER {f2(da)}: precio razonable"

    elif cat == "ciclica":
        pn, cr = m.get("per_normalizado"), m.get("ciclo_ratio")
        pico = cr is not None and cr >= c["ciclica_pico"]
        checks.append(check(f"PER normalizado (ganancia promedio {m.get('ciclo_anios')} años)",
                            None if pn is None else pn <= c["ciclica_per_normalizado_compra"], f1(pn),
                            f"≤ {fn(c['ciclica_per_normalizado_compra'])} compra · > {fn(c['ciclica_per_normalizado_venta'])} venta"))
        checks.append(check("Posición en el ciclo (ganancia actual / promedio)", None if cr is None else not pico,
                            f2(cr), f"< {fn(c['ciclica_pico'])} (≥ = pico)",
                            "En cíclicas, un PER bajo cerca del pico de ganancias es una trampa"))
        if pn is None:
            senal, motivo = VENDER, "Ganancia promedio del ciclo negativa o sin datos"
        elif pn > c["ciclica_per_normalizado_venta"]:
            senal, motivo = VENDER, f"PER normalizado {f1(pn)} > {fn(c['ciclica_per_normalizado_venta'])}" + (" y ganancias en zona de pico" if pico else "")
        elif pn <= c["ciclica_per_normalizado_compra"] and not pico:
            senal, motivo = COMPRAR, f"PER normalizado {f1(pn)} ≤ {fn(c['ciclica_per_normalizado_compra'])} sin estar en pico de ciclo"
        else:
            senal = MANTENER
            motivo = f"PER normalizado {f1(pn)}" + (" pero ganancias en zona de pico: PER bajo engañoso" if pico else "")

    elif cat == "recuperacion":
        if not m.get("ganancia_ttm_positiva"):
            if cn is not None and cn > 0:
                senal, motivo = MANTENER, "Con pérdidas pero caja neta positiva: puede sobrevivir; requiere análisis del caso"
            else:
                senal, motivo = VENDER, "Con pérdidas y deuda neta: Lynch solo compra turnarounds con supervivencia clara"
        else:
            if deuda["estado"] == "no cumple":
                senal, motivo = VENDER, "Saliendo de pérdidas pero con balance apalancado"
            else:
                senal, motivo = MANTENER, "Volvió a ganar dinero; Lynch exige confirmar que la recuperación es sostenible"
    else:
        senal, motivo = MANTENER, "Datos insuficientes para valuar"

    # ── Filtros de calidad que bloquean una compra ──
    if senal == COMPRAR:
        if deuda["estado"] == "no cumple":
            senal, motivo = MANTENER, motivo + " — pero el endeudamiento supera el límite"
        elif inv_mal:
            senal, motivo = MANTENER, motivo + " — pero los inventarios crecen más que las ventas"

    return {"categoria": cat, "categoria_nombre": CATEGORIAS[cat], "categoria_motivo": motivo_cat,
            "senal": senal, "motivo": motivo, "checks": checks, "alertas": alertas}
