#!/usr/bin/env python3
"""
verificar_ws.py - Chequea que el MODO 2 (WebSocket) este emitiendo bien, y mide
lo mismo que el firmware reporta por Serial.

Verifica exactamente lo que la tabla de Wireshark del README promete:
  - handshake con 101 Switching Protocols y Sec-WebSocket-Accept correcto
  - un JPEG completo por mensaje BINARIO, rearmando frames de continuacion
  - servidor -> cliente SIN mascara (MASK=0)
  - sin ping/pong intercalado

Uso:
    python3 verificar_ws.py 192.168.4.84
    python3 verificar_ws.py 192.168.4.84 --puerto 80 --ruta /stream --segundos 15

No usa librerias externas: habla WebSocket a mano.
"""
import argparse
import base64
import hashlib
import os
import socket
import sys
import time

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exacto(sock, buf, n):
    while len(buf) < n:
        d = sock.recv(65536)
        if not d:
            return None, buf
        buf += d
    return buf[:n], buf[n:]


def leer_frame(sock, buf):
    """Devuelve (fin, opcode, enmascarado, payload, resto) o (None,...)."""
    cab, buf = _recv_exacto(sock, buf, 2)
    if cab is None:
        return None, None, None, None, buf
    fin = (cab[0] & 0x80) != 0
    opcode = cab[0] & 0x0F
    enmascarado = (cab[1] & 0x80) != 0
    n = cab[1] & 0x7F
    if n == 126:
        ext, buf = _recv_exacto(sock, buf, 2)
        if ext is None:
            return None, None, None, None, buf
        n = int.from_bytes(ext, "big")
    elif n == 127:
        ext, buf = _recv_exacto(sock, buf, 8)
        if ext is None:
            return None, None, None, None, buf
        n = int.from_bytes(ext, "big")
    if enmascarado:
        mk, buf = _recv_exacto(sock, buf, 4)
        if mk is None:
            return None, None, None, None, buf
    cuerpo, buf = _recv_exacto(sock, buf, n)
    if cuerpo is None:
        return None, None, None, None, buf
    return fin, opcode, enmascarado, cuerpo, buf


def verificar(host, puerto, ruta, segundos):
    errores = []
    sock = socket.create_connection((host, puerto), timeout=15)
    sock.settimeout(15)

    clave = base64.b64encode(os.urandom(16))
    pedido = (f"GET {ruta} HTTP/1.1\r\n"
              f"Host: {host}:{puerto}\r\n"
              f"Upgrade: websocket\r\n"
              f"Connection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {clave.decode()}\r\n"
              f"Sec-WebSocket-Version: 13\r\n\r\n").encode()
    sock.sendall(pedido)

    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sock.recv(65536)
        if not d:
            print("el servidor cerro durante el handshake")
            return 1
        buf += d
    i = buf.index(b"\r\n\r\n")
    cabecera, buf = buf[:i], buf[i + 4:]

    print("--- handshake ---")
    print(cabecera.decode("latin-1").strip())
    print("-----------------")

    lineas = cabecera.split(b"\r\n")
    if b"101" not in lineas[0]:
        print(f"FALLO: se esperaba 101 Switching Protocols, llego: {lineas[0]!r}")
        return 1

    esperado = base64.b64encode(hashlib.sha1(clave + GUID).digest())
    acc = next((l.split(b":", 1)[1].strip() for l in lineas
                if l.lower().startswith(b"sec-websocket-accept")), None)
    if acc != esperado:
        errores.append(f"Sec-WebSocket-Accept incorrecto: {acc!r} != {esperado!r}")
    else:
        print("Sec-WebSocket-Accept: correcto")

    n_bin = n_frames = n_frag = n_mask = 0
    n_texto = n_control = 0
    total = 0
    tam_min = None
    tam_max = 0
    otros_opcodes = {}
    mensaje = None

    def registrar_jpeg(cuerpo):
        nonlocal n_bin, total, tam_min, tam_max
        n_bin += 1
        total += len(cuerpo)
        tam_max = max(tam_max, len(cuerpo))
        tam_min = len(cuerpo) if tam_min is None else min(tam_min, len(cuerpo))
        if not cuerpo.startswith(b"\xff\xd8"):
            errores.append(f"mensaje {n_bin}: no arranca con SOI (FFD8)")
        if not cuerpo.rstrip().endswith(b"\xff\xd9"):
            errores.append(f"mensaje {n_bin}: no termina con EOI (FFD9)")

    t0 = time.time()
    while time.time() - t0 < segundos:
        fin, opcode, enmascarado, cuerpo, buf = leer_frame(sock, buf)
        if fin is None:
            break
        n_frames += 1
        if enmascarado:
            n_mask += 1

        if opcode == 0x2:
            if mensaje is not None:
                errores.append("llego un nuevo mensaje binario antes de terminar el anterior")
            mensaje = bytearray(cuerpo)
            if not fin:
                n_frag += 1
            else:
                registrar_jpeg(bytes(mensaje))
                mensaje = None
        elif opcode == 0x0:
            if mensaje is None:
                errores.append("frame de continuacion sin mensaje previo")
            else:
                mensaje.extend(cuerpo)
                if fin:
                    registrar_jpeg(bytes(mensaje))
                    mensaje = None
        elif opcode == 0x1:
            n_texto += 1       # confirmacion de configuracion del servidor
        elif opcode == 0x8:
            print("el servidor cerro la conexion (opcode 0x8)")
            break
        elif opcode in (0x9, 0xA):
            n_control += 1
        else:
            otros_opcodes[opcode] = otros_opcodes.get(opcode, 0) + 1

    dur = time.time() - t0
    try:
        sock.close()
    except OSError:
        pass

    print()
    print(f"mensajes binarios : {n_bin}")
    print(f"frames WebSocket  : {n_frames}")
    print(f"duracion          : {dur:.1f} s")
    if n_bin and dur > 0:
        print(f"fps observado     : {n_bin / dur:.2f}")
        print(f"bitrate           : {total * 8 / dur / 1000:.0f} kbit/s "
              f"({total / dur / 1024:.0f} KiB/s)")
        print(f"tam JPEG          : min {tam_min} / prom {total // n_bin} / max {tam_max} bytes")
    print(f"mensajes fragment.: {n_frag}   (valido: se rearman antes de validar el JPEG)")
    print(f"mensajes de texto : {n_texto}   (configuracion/confirmacion)")
    print(f"ping/pong         : {n_control}   (tiene que ser 0: auto-ping apagado)")
    print(f"enmascarados      : {n_mask}   (tiene que ser 0: servidor->cliente no enmascara)")
    print(f"otros opcodes     : {otros_opcodes if otros_opcodes else '{}'}")

    if n_control:
        errores.append(f"{n_control} frames ping/pong inesperados")
    if n_mask:
        errores.append(f"{n_mask} frames enmascarados desde el servidor")
    if n_bin == 0:
        errores.append("no llego ningun mensaje binario")

    print()
    if errores:
        print("FALLO:")
        for e in errores:
            print(f"  - {e}")
        return 1
    print("OK: handshake valido y un JPEG completo por mensaje binario.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--puerto", type=int, default=80)
    ap.add_argument("--ruta", default="/stream")
    ap.add_argument("--segundos", type=float, default=12.0)
    a = ap.parse_args()
    return verificar(a.host, a.puerto, a.ruta, a.segundos)


if __name__ == "__main__":
    sys.exit(main())
