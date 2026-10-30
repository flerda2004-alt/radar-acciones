"""Genera la página estática (index.html) con los datos embebidos."""
from __future__ import annotations

import json
from pathlib import Path

PLANTILLA = Path(__file__).with_name("plantilla.html")

ENVOLTORIO = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}body{{margin:0}}[hidden]{{display:none!important}}</style>
{cabeza}
</head>
<body>
{cuerpo}
</body>
</html>
"""


def construir(data: dict, destino: Path, completo: bool = True) -> None:
    js = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
    js = js.replace("</", "<\\/")  # evita cerrar el <script> desde los datos
    contenido = PLANTILLA.read_text(encoding="utf-8").replace("__DATOS__", js)
    if completo:
        corte = contenido.index("</style>") + len("</style>")
        html = ENVOLTORIO.format(cabeza=contenido[:corte], cuerpo=contenido[corte:])
    else:
        html = contenido
    destino.write_text(html, encoding="utf-8")
