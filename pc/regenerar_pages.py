#!/usr/bin/env python3
"""Re-extrae pages.py desde los .cpp del firmware.

Correr si se toca el HTML de mode_http_mjpeg.cpp o mode_websocket.cpp, para que
las dos plataformas sigan sirviendo exactamente la misma pagina.

Reemplaza SOLO lo que esta debajo de la linea MARCA; la cabecera y la funcion
pagina_mjpeg() quedan intactas.
"""
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "pc" / "pages.py"
MARCA = "# ---------- GENERADO POR regenerar_pages.py: no editar debajo ----------"


def extraer(nombre):
    texto = (RAIZ / nombre).read_text()
    m = re.search(r'R"HTML\((.*?)\)HTML"', texto, re.S)
    if not m:
        sys.exit(f"no encontre el bloque R\"HTML(...)HTML\" en {nombre}")
    return m.group(1)


def main():
    h1 = extraer("mode_http_mjpeg.cpp").replace(":81/stream", "__PUERTO_STREAM__/stream")
    h2 = extraer("mode_websocket.cpp")

    if "__PUERTO_STREAM__" not in h1:
        sys.exit("la pagina del modo 1 ya no dice ':81/stream': revisar el "
                 "reemplazo del puerto en este script")
    for nombre, html in (("modo 1", h1), ("modo 2", h2)):
        if "<!doctype html>" not in html.lower():
            sys.exit(f"lo extraido para el {nombre} no parece HTML")

    actual = DESTINO.read_text()
    if MARCA not in actual:
        sys.exit(f"falta la linea marca en {DESTINO}; no toco nada")
    cabecera = actual.split(MARCA)[0]

    DESTINO.write_text(
        cabecera + MARCA + "\n\n"
        + "INDEX_HTML_MJPEG = r'''" + h1 + "'''\n\n"
        + "INDEX_HTML_WS = r'''" + h2 + "'''\n")
    print(f"pages.py regenerado: modo1 {len(h1)} B, modo2 {len(h2)} B")


if __name__ == "__main__":
    main()
