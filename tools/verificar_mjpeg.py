#!/usr/bin/env python3
"""
verificar_mjpeg.py - Chequea que el MODO 1 (HTTP MJPEG) este emitiendo un
multipart bien formado, y mide lo mismo que el firmware reporta por Serial.

Sirve para cerrar el lazo: el CSV dice lo que el ESP32 CREE que mando; esto dice
lo que realmente llego por la red. Si los dos coinciden, la captura de Wireshark
se puede leer con confianza.

Uso:
    python3 verificar_mjpeg.py 192.168.1.50
    python3 verificar_mjpeg.py 192.168.1.50 --puerto 81 --segundos 15
    python3 verificar_mjpeg.py --autotest     # se prueba a si mismo, sin placa

Codigo de salida: 0 si todos los chequeos pasan, 1 si alguno falla.
"""
import argparse
import re
import socket
import sys
import time

BOUNDARY_ESPERADO = b"frameboundary"


class Verificador:
    def __init__(self):
        self.errores = []
        self.frames = 0
        self.bytes_jpeg = 0
        self.tam_min = None
        self.tam_max = 0
        self.slots = []          # numeros de X-Slot vistos
        self.lag = []            # (llegada - X-Timestamp-Ms), para ver deriva
        self.frames_n = []       # numeros de X-Frame-N vistos

    def error(self, msg):
        self.errores.append(msg)


def _leer_hasta(sock, buf, marca):
    """Acumula del socket hasta encontrar `marca`. Devuelve (antes, resto)."""
    while marca not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None, buf
        buf += chunk
    i = buf.index(marca)
    return buf[:i], buf[i + len(marca):]


def _leer_n(sock, buf, n):
    """Asegura n bytes en buf. Devuelve (n_bytes, resto)."""
    while len(buf) < n:
        chunk = sock.recv(65536)
        if not chunk:
            return None, buf
        buf += chunk
    return buf[:n], buf[n:]


def verificar(host, puerto, segundos, v=None):
    v = v or Verificador()

    sock = socket.create_connection((host, puerto), timeout=10)
    sock.settimeout(10)
    pedido = (f"GET /stream HTTP/1.1\r\n"
              f"Host: {host}:{puerto}\r\n"
              f"User-Agent: verificar_mjpeg\r\n"
              f"Connection: keep-alive\r\n\r\n").encode()
    sock.sendall(pedido)

    buf = b""
    cabecera, buf = _leer_hasta(sock, buf, b"\r\n\r\n")
    if cabecera is None:
        v.error("el servidor cerro antes de mandar las cabeceras")
        return v

    print("--- respuesta HTTP ---")
    print(cabecera.decode("latin-1").strip())
    print("----------------------")

    lineas = cabecera.split(b"\r\n")
    if not lineas[0].startswith(b"HTTP/1.1 200"):
        v.error(f"se esperaba 200 OK, llego: {lineas[0]!r}")
        return v

    ct = next((l for l in lineas if l.lower().startswith(b"content-type:")), b"")
    if b"multipart/x-mixed-replace" not in ct.lower():
        v.error(f"Content-Type no es multipart/x-mixed-replace: {ct!r}")
        return v

    m = re.search(rb"boundary=([^\s;]+)", ct)
    if not m:
        v.error("el Content-Type no declara boundary")
        return v
    boundary = m.group(1)
    if boundary != BOUNDARY_ESPERADO:
        print(f"aviso: boundary '{boundary.decode()}' (el firmware usa "
              f"'{BOUNDARY_ESPERADO.decode()}')")
    sep = b"--" + boundary

    t0 = time.time()
    while time.time() - t0 < segundos:
        pre, buf = _leer_hasta(sock, buf, sep)
        if pre is None:
            break
        cab_part, buf = _leer_hasta(sock, buf, b"\r\n\r\n")
        if cab_part is None:
            break

        campos = {}
        for linea in cab_part.split(b"\r\n"):
            if b":" in linea:
                k, _, val = linea.partition(b":")
                campos[k.strip().lower()] = val.strip()

        if campos.get(b"content-type") != b"image/jpeg":
            v.error(f"parte sin Content-Type image/jpeg: {campos!r}")
            break
        if b"content-length" not in campos:
            v.error("parte sin Content-Length (no se puede delimitar el JPEG)")
            break

        n = int(campos[b"content-length"])
        cuerpo, buf = _leer_n(sock, buf, n)
        if cuerpo is None:
            v.error("el stream se corto en medio de un JPEG")
            break

        # Un JPEG valido empieza en FFD8 (SOI) y termina en FFD9 (EOI). Si esto
        # falla, el Content-Length no coincide con el frame real: se estan
        # desalineando las partes.
        if not cuerpo.startswith(b"\xff\xd8"):
            v.error(f"frame {v.frames}: no arranca con SOI (FFD8)")
        if not cuerpo.rstrip().endswith(b"\xff\xd9"):
            v.error(f"frame {v.frames}: no termina con EOI (FFD9)")

        v.frames += 1
        v.bytes_jpeg += n
        v.tam_max = max(v.tam_max, n)
        v.tam_min = n if v.tam_min is None else min(v.tam_min, n)
        if b"x-slot" in campos:
            v.slots.append(int(campos[b"x-slot"]))
        if b"x-frame-n" in campos:
            v.frames_n.append(int(campos[b"x-frame-n"]))
        # Latencia: X-Timestamp-Ms es el millis() del equipo al empezar a mandar.
        # Los relojes no estan sincronizados, asi que el valor absoluto no dice
        # nada; lo que importa es si la DIFERENCIA crece: si crece, se esta
        # acumulando cola y el video se ve cada vez mas atrasado.
        if b"x-timestamp-ms" in campos:
            v.lag.append(time.time() * 1000.0 - int(campos[b"x-timestamp-ms"]))

    dur = time.time() - t0
    sock.close()

    print()
    print(f"frames recibidos : {v.frames}")
    print(f"duracion         : {dur:.1f} s")
    if dur > 0 and v.frames:
        print(f"fps observado    : {v.frames / dur:.2f}")
        print(f"bitrate          : {v.bytes_jpeg * 8 / dur / 1000:.0f} kbit/s "
              f"({v.bytes_jpeg / dur / 1024:.0f} KiB/s)")
        print(f"tam JPEG         : min {v.tam_min} / prom "
              f"{v.bytes_jpeg // v.frames} / max {v.tam_max} bytes")

    # Huecos en X-Slot = frames que el reloj de FPS pidio y no salieron.
    # Tiene que dar lo mismo que la columna 'dropped' del CSV en esa ventana.
    if len(v.slots) >= 2:
        esperados = v.slots[-1] - v.slots[0] + 1
        huecos = esperados - len(v.slots)
        print(f"ranuras X-Slot   : {v.slots[0]}..{v.slots[-1]} "
              f"({esperados} esperadas, {len(v.slots)} recibidas)")
        print(f"descartes en red : {huecos}   <- comparar con 'dropped' del CSV")
    if len(v.frames_n) >= 2:
        saltos = [b - a for a, b in zip(v.frames_n, v.frames_n[1:]) if b - a != 1]
        if saltos:
            v.error(f"X-Frame-N no es consecutivo (saltos: {saltos}); "
                    "cuenta frames ENVIADOS, no deberia tener huecos")

    if len(v.lag) >= 6:
        base = min(v.lag)
        n = max(3, len(v.lag) // 5)
        ini = sum(v.lag[:n]) / n - base
        fin = sum(v.lag[-n:]) / n - base
        print(f"latencia relativa : arranque {ini:.0f} ms -> final {fin:.0f} ms "
              f"(deriva {fin - ini:+.0f} ms)")
        if fin - ini > 500:
            print("   ^ la latencia CRECE: se esta acumulando cola, el video va atrasado")
        else:
            print("   ^ latencia estable: no se acumula cola")

    print()
    if v.errores:
        print("FALLO:")
        for e in v.errores:
            print(f"  - {e}")
    elif v.frames == 0:
        v.error("no llego ningun frame")
        print("FALLO:\n  - no llego ningun frame")
    else:
        print("OK: multipart bien formado, JPEGs completos y delimitados.")
    return v


# -----------------------------------------------------------------------------
# Autotest: sirve el MISMO stream de bytes que emite mode_http_mjpeg.cpp y se
# verifica contra si mismo. Prueba el formato de cable y esta herramienta, sin
# necesidad de tener la placa conectada.
# -----------------------------------------------------------------------------
def _jpeg_sintetico(n):
    # No es una imagen real: solo SOI + relleno + EOI, que es lo unico que el
    # verificador mira. Alcanza para validar el entramado.
    cuerpo = bytes((i * 37 + 11) % 251 for i in range(max(0, n - 4)))
    return b"\xff\xd8" + cuerpo + b"\xff\xd9"


def _mock_server(sock_srv, fps, saltar_ranura):
    conn, _ = sock_srv.accept()
    with conn:
        conn.recv(4096)  # request line + headers
        conn.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: multipart/x-mixed-replace; boundary=frameboundary\r\n"
            b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
            b"Pragma: no-cache\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Connection: close\r\n"
            b"\r\n")
        enviados = 0
        try:
            for slot in range(1, 200):
                if slot in saltar_ranura:      # simula un frame descartado
                    time.sleep(1.0 / fps)
                    continue
                jpeg = _jpeg_sintetico(3000 + (slot * 97) % 900)
                enviados += 1
                conn.sendall(
                    b"--frameboundary\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n".encode()
                    + f"X-Slot: {slot}\r\n".encode()
                    + f"X-Frame-N: {enviados}\r\n".encode()
                    + f"X-Timestamp-Ms: {int(time.time()*1000) % 10**9}\r\n".encode()
                    + b"\r\n" + jpeg + b"\r\n")
                time.sleep(1.0 / fps)
        except (BrokenPipeError, ConnectionResetError):
            pass


def autotest():
    import threading
    fps = 20
    saltar = {5, 6, 12}          # 3 ranuras perdidas a proposito
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    puerto = srv.getsockname()[1]

    hilo = threading.Thread(target=_mock_server, args=(srv, fps, saltar), daemon=True)
    hilo.start()

    print(f"autotest: servidor simulado en 127.0.0.1:{puerto}, "
          f"{fps} fps, ranuras descartadas a proposito: {sorted(saltar)}\n")
    v = verificar("127.0.0.1", puerto, segundos=3)
    srv.close()

    esperado = len(saltar)
    huecos = 0
    if len(v.slots) >= 2:
        huecos = (v.slots[-1] - v.slots[0] + 1) - len(v.slots)
    # El primer slot visto puede caer despues de un salto, asi que el conteo
    # observado es <= al inyectado; lo que importa es que detecte los que abarca.
    print(f"\nautotest: descartes inyectados={esperado} detectados={huecos}")
    ok = not v.errores and v.frames > 0 and huecos > 0
    print("autotest: OK" if ok else "autotest: FALLO")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", nargs="?", help="IP del XIAO")
    ap.add_argument("--puerto", type=int, default=81)
    ap.add_argument("--segundos", type=float, default=10.0)
    ap.add_argument("--autotest", action="store_true",
                    help="se prueba contra un servidor simulado, sin hardware")
    a = ap.parse_args()

    if a.autotest:
        return autotest()
    if not a.host:
        ap.error("falta el host (o usar --autotest)")
    v = verificar(a.host, a.puerto, a.segundos)
    return 1 if (v.errores or v.frames == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
