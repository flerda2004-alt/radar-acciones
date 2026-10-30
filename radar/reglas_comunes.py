"""Constantes y helpers compartidos por los motores de Lynch y Graham."""
from __future__ import annotations

COMPRAR, MANTENER, VENDER, NO_APLICA = "COMPRAR", "MANTENER", "VENDER", "N/A"
PUNTOS = {COMPRAR: 1, MANTENER: 0, VENDER: -1}


def check(nombre: str, ok, valor: str, umbral: str, nota: str = "", informativo: bool = False) -> dict:
    estado = "sin datos" if ok is None else ("cumple" if ok else "no cumple")
    return {"nombre": nombre, "estado": estado, "valor": valor, "umbral": umbral, "nota": nota,
            "informativo": informativo}


def _fmt(v, dec):
    if v is None:
        return "s/d"
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def f1(v):
    return _fmt(v, 1)


def f2(v):
    return _fmt(v, 2)


def fn(v):
    """Número de parámetro: entero sin decimales, si no con coma decimal."""
    if v is None:
        return "s/d"
    v = float(v)
    return str(int(v)) if v.is_integer() else _fmt(v, 2).rstrip("0").rstrip(",")


def pct(v):
    return "s/d" if v is None else _fmt(v, 1) + "%"


def combinar(lynch: str | None, graham: str | None) -> tuple[str, str]:
    """Suma simple: COMPRAR=+1, MANTENER=0, VENDER=−1."""
    if lynch is None and graham is None:
        return NO_APLICA, "No aplica"
    p = PUNTOS.get(lynch, 0) + PUNTOS.get(graham, 0)
    if p >= 1:
        senal = COMPRAR
    elif p <= -1:
        senal = VENDER
    else:
        senal = MANTENER
    if lynch == graham:
        motivo = f"Lynch y Graham coinciden: {lynch.lower()}"
    elif {lynch, graham} == {COMPRAR, VENDER}:
        motivo = "Señales opuestas: Lynch " + lynch.lower() + ", Graham " + graham.lower()
    else:
        motivo = f"Lynch {lynch.lower()} · Graham {graham.lower()}"
    return senal, motivo
