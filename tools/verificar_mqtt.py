#!/usr/bin/env python3
"""
verificar_mqtt.py - Chequea que el MODO 3 (MQTT) este publicando bien, y mide
lo mismo que el emisor reporta por Serial / stdout.

Sirve igual contra el firmware del ESP32 y contra el port a PC (pc/camara_pc.py):
los dos hablan el mismo MQTT 3.1.1 con QoS 0.

Verifica exactamente lo que la tabla de Wireshark del README promete:
  - CONNECT / CONNACK y SUBSCRIBE / SUBACK contra el broker
  - telemetria en camara/telemetria a 1 Hz, con el esquema JSON completo
  - imagenes en camara/imagen: JPEG completo, SOI/EOI correctos
  - QoS 0 en los PUBLISH entrantes (sin PUBACK)
  - el comando snapshot responde con una imagen

Cliente MQTT propio, solo stdlib: igual que los otros dos verificadores, no
necesita instalar nada.

Uso:
    python3 verificar_mqtt.py                      # broker en localhost
    python3 verificar_mqtt.py 192.168.4.58 --segundos 20
    python3 verificar_mqtt.py --snapshot           # ademas pide una foto
"""
import argparse
import json
import socket
import struct
import sys
import time

CONNECT, CONNACK = 0x10, 0x20
PUBLISH = 0x30
SUBSCRIBE, SUBACK = 0x82, 0x90
PINGREQ, PINGRESP = 0xC0, 0xD0

CAMPOS_TELEMETRIA = {"fps", "bytes_s", "rssi", "heap_free", "dropped",
                     "frames", "uptime_s", "drop_detail"}
CAMPOS_DROP = {"sched", "cap", "bp", "noclient", "tx"}


def _varint(n):
    out = b""
    while True:
        d = n % 128
        n //= 128
        out += bytes([d | (0x80 if n > 0 else 0)])
        if n == 0:
            return out


def _cadena(s):
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def _leer_varint(b, i):
    mult, valor = 1, 0
    while True:
        if i >= len(b):
            return None, i
        d = b[i]
        i += 1
        valor += (d & 0x7F) * mult
        if not d & 0x80:
            return valor, i
        mult *= 128


class Cliente:
    def __init__(self, host, puerto, client_id):
        self.sock = socket.create_connection((host, puerto), timeout=5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        vh = _cadena("MQTT") + bytes([0x04, 0x02]) + struct.pack(">H", 30)
        cuerpo = vh + _cadena(client_id)
        self.sock.sendall(bytes([CONNECT]) + _varint(len(cuerpo)) + cuerpo)
        r = self.sock.recv(4)
        if len(r) < 4 or r[0] != CONNACK:
            raise OSError(f"CONNACK invalido: {r!r}")
        if r[3] != 0:
            raise OSError(f"el broker rechazo la conexion, codigo {r[3]}")

    def suscribir(self, topicos):
        pid = 1
        cuerpo = struct.pack(">H", pid)
        for t in topicos:
            cuerpo += _cadena(t) + bytes([0])
        self.sock.sendall(bytes([SUBSCRIBE]) + _varint(len(cuerpo)) + cuerpo)
        r = self.sock.recv(16)
        if len(r) < 4 or r[0] != SUBACK:
            raise OSError(f"SUBACK invalido: {r!r}")

    def publicar(self, topico, payload):
        vh = _cadena(topico)
        self.sock.sendall(bytes([PUBLISH]) + _varint(len(vh) + len(payload)) + vh + payload)

    def recibir(self, timeout):
        """Devuelve (topico, payload, qos) o None si no llego nada."""
        fin = time.monotonic() + timeout
        while True:
            while True:
                if len(self.buf) < 2:
                    break
                tipo = self.buf[0] & 0xF0
                qos = (self.buf[0] >> 1) & 0x03
                rem, i = _leer_varint(self.buf, 1)
                if rem is None or len(self.buf) < i + rem:
                    break
                cuerpo = self.buf[i:i + rem]
                self.buf = self.buf[i + rem:]
                if tipo == PUBLISH:
                    tl = struct.unpack(">H", cuerpo[:2])[0]
                    topico = cuerpo[2:2 + tl].decode("utf-8", "replace")
                    payload = cuerpo[2 + tl:]
                    if qos > 0:
                        payload = payload[2:]      # packet id
                    return topico, payload, qos

            resto = fin - time.monotonic()
            if resto <= 0:
                return None
            self.sock.settimeout(resto)
            try:
                t = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            if not t:
                return None
            self.buf += t

    def cerrar(self):
        try:
            self.sock.sendall(bytes([0xE0, 0x00]))
            self.sock.close()
        except OSError:
            pass


def verificar(host, puerto, segundos, t_telemetria, t_imagen, t_cmd, pedir_snapshot):
    errores = []
    print(f"conectando al broker {host}:{puerto} ...")
    try:
        c = Cliente(host, puerto, f"verificador-{int(time.time()) % 100000}")
    except OSError as e:
        print(f"FALLO: no pude conectarme al broker: {e}")
        print("  - esta corriendo?  mosquitto -c tools/mosquitto.conf -v")
        return 1
    print("CONNECT / CONNACK: ok")
    c.suscribir([t_telemetria, t_imagen])
    print(f"SUBSCRIBE / SUBACK: ok  ({t_telemetria}, {t_imagen})")

    if pedir_snapshot:
        c.publicar(t_cmd, b"snapshot")
        print(f"publicado 'snapshot' en {t_cmd}")

    n_telem = n_img = 0
    bytes_img = 0
    tam = []
    qos_malos = 0
    ts_telem = []
    ultimo_uptime = None
    t0 = time.monotonic()

    while time.monotonic() - t0 < segundos:
        msg = c.recibir(min(2.0, segundos - (time.monotonic() - t0)))
        if msg is None:
            continue
        topico, payload, qos = msg
        if qos != 0:
            qos_malos += 1

        if topico == t_telemetria:
            n_telem += 1
            ts_telem.append(time.monotonic())
            try:
                d = json.loads(payload)
            except ValueError as e:
                errores.append(f"telemetria no es JSON valido: {e}")
                continue
            faltan = CAMPOS_TELEMETRIA - set(d)
            if faltan:
                errores.append(f"a la telemetria le faltan campos: {sorted(faltan)}")
            if isinstance(d.get("drop_detail"), dict):
                f2 = CAMPOS_DROP - set(d["drop_detail"])
                if f2:
                    errores.append(f"a drop_detail le faltan campos: {sorted(f2)}")
            else:
                errores.append("drop_detail no es un objeto")
            up = d.get("uptime_s")
            if ultimo_uptime is not None and isinstance(up, int) and up < ultimo_uptime:
                errores.append("uptime_s retrocedio: se reinicio el emisor?")
            ultimo_uptime = up

        elif topico == t_imagen:
            n_img += 1
            bytes_img += len(payload)
            tam.append(len(payload))
            if not payload.startswith(b"\xff\xd8"):
                errores.append(f"imagen {n_img}: no arranca con SOI (FFD8)")
            if not payload.rstrip().endswith(b"\xff\xd9"):
                errores.append(f"imagen {n_img}: no termina con EOI (FFD9)")

    dur = time.monotonic() - t0
    c.cerrar()

    print()
    print(f"duracion            : {dur:.1f} s")
    print(f"telemetria recibida : {n_telem} mensajes ({n_telem / dur:.2f}/s, se espera ~1.00/s)")
    print(f"imagenes recibidas  : {n_img}")
    if n_img:
        print(f"fps de imagen       : {n_img / dur:.2f}")
        print(f"bitrate             : {bytes_img * 8 / dur / 1000:.0f} kbit/s "
              f"({bytes_img / dur / 1024:.0f} KiB/s)")
        print(f"tam JPEG            : min {min(tam)} / prom {bytes_img // n_img} "
              f"/ max {max(tam)} bytes")
    print(f"PUBLISH con QoS > 0 : {qos_malos}   (tiene que ser 0: el emisor usa QoS 0)")

    if len(ts_telem) >= 3:
        gaps = [b - a for a, b in zip(ts_telem, ts_telem[1:])]
        print(f"periodo telemetria  : min {min(gaps):.2f} s / max {max(gaps):.2f} s "
              f"(nominal 1.00 s)")
        if max(gaps) > 3.0:
            errores.append(f"hubo un hueco de {max(gaps):.1f} s en la telemetria")

    if qos_malos:
        errores.append(f"{qos_malos} PUBLISH con QoS distinto de 0")
    if n_telem == 0:
        errores.append("no llego ninguna telemetria: el emisor esta conectado al broker?")
    if pedir_snapshot and n_img == 0:
        errores.append("se pidio snapshot y no llego ninguna imagen")

    print()
    if errores:
        print("FALLO:")
        for e in dict.fromkeys(errores):
            print(f"  - {e}")
        return 1
    print("OK: dialogo MQTT correcto, telemetria completa y JPEGs integros.")
    if n_img == 0:
        print("   (sin imagenes: normal si MQTT_STREAM_ENABLED esta en 0. "
              "Probar --snapshot, o publicar 'stream:on' en camara/cmd)")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", nargs="?", default="127.0.0.1", help="IP del broker")
    p.add_argument("--puerto", type=int, default=1883)
    p.add_argument("--segundos", type=float, default=12.0)
    p.add_argument("--telemetria", default="camara/telemetria")
    p.add_argument("--imagen", default="camara/imagen")
    p.add_argument("--cmd", default="camara/cmd")
    p.add_argument("--snapshot", action="store_true",
                   help="publica 'snapshot' y verifica que llegue la imagen")
    a = p.parse_args()
    return verificar(a.host, a.puerto, a.segundos, a.telemetria, a.imagen,
                     a.cmd, a.snapshot)


if __name__ == "__main__":
    sys.exit(main())
