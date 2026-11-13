"""
mode_websocket.py  -  Espejo de mode_websocket.cpp.

MODO 2  -  WebSocket binario. El equipo es SERVIDOR.

Puerto 80: sirve la pagina cliente Y hace el upgrade a WebSocket en /stream.
Un JPEG completo por mensaje binario, sin fragmentar en varios mensajes.

Lo que se busca ver en la captura:
  - el handshake HTTP con "101 Switching Protocols" limpio, una sola vez
  - de ahi en mas, frames WebSocket con opcode 0x2 (binario) sobre la misma
    conexion TCP
  - sin ping/pong: no se manda auto-ping, asi que TODO frame WS que aparezca es
    video o control explicito

El framing WS esta escrito a mano (no se usa una libreria) por el mismo motivo
por el que el firmware controla cada write(): para decidir exactamente que bytes
salen al cable. Son ~40 lineas y evita que una libreria agregue ping/pong,
fragmentacion o compresion permessage-deflate, que arruinarian la captura.

DIFERENCIA de medicion con el firmware, importante para el informe:
  En el ESP32 la libreria es ASINCRONICA: binary() encola y vuelve enseguida,
  asi que la fila del CSV se DIFIERE hasta que la cola drena. Aca el socket es
  bloqueante, de modo que el sendall() ya ES el tiempo hasta soltar el frame a
  la red. Las dos cosas miden lo mismo ("cuanto tardo en salir"), por caminos
  distintos. El equivalente de la cola llena es el socket no escribible, y se
  cuenta igual como 'bp'.
"""
import base64
import hashlib
import json
import select
import socket
import struct

import config
from frame_clock import FrameClock
from metrics import (DROP_BACKPRESSURE, DROP_CAPTURE, DROP_TRANSPORT, g_m,
                     log_comment, metrics_frame_dropped, metrics_frame_sent,
                     metrics_to_json, millis)
from pages import INDEX_HTML_WS

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# Solo estas resoluciones acepta el control, igual que parseFrameSize() del
# firmware.
RESOLUCIONES_CONTROL = ("QVGA", "VGA", "SVGA")

_srv = None
_puerto = None
_cam = None
_clock = FrameClock()

_ws_sock = None          # el unico espectador
_ws_addr = None
_ws_buf = b""            # bytes del cliente todavia sin parsear
_pend = []               # conexiones que aun no completaron su request HTTP

_target_fps = None
_mandar_config = False


# -----------------------------------------------------------------------------
# Framing WebSocket
# -----------------------------------------------------------------------------
def _armar_frame(opcode, payload):
    """Frame servidor->cliente: FIN=1, sin mascara (lo manda el RFC 6455: solo
    el cliente enmascara). Un mensaje por frame, nunca fragmentado."""
    n = len(payload)
    cab = bytes([0x80 | opcode])
    if n < 126:
        cab += bytes([n])
    elif n <= 0xFFFF:
        cab += b"\x7e" + struct.pack(">H", n)
    else:
        cab += b"\x7f" + struct.pack(">Q", n)
    return cab + payload


def _leer_frame(buf):
    """(opcode, payload, resto) o (None, None, buf) si falta data."""
    if len(buf) < 2:
        return None, None, buf
    b0, b1 = buf[0], buf[1]
    opcode = b0 & 0x0F
    enmascarado = bool(b1 & 0x80)
    ln = b1 & 0x7F
    i = 2
    if ln == 126:
        if len(buf) < 4:
            return None, None, buf
        ln = struct.unpack(">H", buf[2:4])[0]
        i = 4
    elif ln == 127:
        if len(buf) < 10:
            return None, None, buf
        ln = struct.unpack(">Q", buf[2:10])[0]
        i = 10
    mascara = b""
    if enmascarado:
        if len(buf) < i + 4:
            return None, None, buf
        mascara = buf[i:i + 4]
        i += 4
    if len(buf) < i + ln:
        return None, None, buf
    cuerpo = bytearray(buf[i:i + ln])
    if enmascarado:
        for k in range(ln):
            cuerpo[k] ^= mascara[k & 3]
    return opcode, bytes(cuerpo), buf[i + ln:]


def _enviar_ws(opcode, payload):
    """True si salio entero."""
    try:
        _ws_sock.sendall(_armar_frame(opcode, payload))
        return True
    except (TimeoutError, socket.timeout, OSError):
        return False


def _escribible():
    """El equivalente de queueLen()==0 del firmware: si el socket no acepta mas
    datos, el frame nuevo se descarta en vez de encolarse."""
    try:
        _, w, _ = select.select([], [_ws_sock], [], 0)
        return bool(w)
    except OSError:
        return False


# -----------------------------------------------------------------------------
# Control: mismo protocolo de texto que el firmware
# -----------------------------------------------------------------------------
def _enviar_config():
    _enviar_ws(OP_TEXT, json.dumps(
        {"type": "config", "resolution": _cam.framesize_name, "fps": _target_fps},
        separators=(",", ":")).encode())


def _enviar_error(msg):
    _enviar_ws(OP_TEXT, json.dumps(
        {"type": "error", "message": msg}, separators=(",", ":")).encode())


def _procesar_comando(texto):
    global _target_fps, _mandar_config
    cmd = texto[:40]
    if cmd == "config:get":
        _mandar_config = True
    elif cmd.startswith("fps:"):
        try:
            fps = int(cmd[4:])
        except ValueError:
            fps = 0
        if fps < 1 or fps > 30:
            _enviar_error("FPS debe estar entre 1 y 30")
        else:
            _target_fps = fps
            intervalo = max(1, 1000 // fps)
            _clock.begin(intervalo)
            config.FRAME_INTERVAL_MS = intervalo
            log_comment("ws: FPS objetivo cambiado a %d por mensaje WebSocket", fps)
            _mandar_config = True
    elif cmd.startswith("resolution:"):
        nombre = cmd[11:]
        if nombre not in RESOLUCIONES_CONTROL:
            _enviar_error("resolucion valida: QVGA, VGA o SVGA")
        elif _cam.set_framesize(nombre):
            _clock.resync()
            log_comment("ws: resolucion cambiada a %s por mensaje WebSocket", nombre)
            _mandar_config = True
        else:
            _enviar_error("la camara rechazo la resolucion")
    else:
        _enviar_error("comando desconocido")


# -----------------------------------------------------------------------------
# Handshake y HTTP del puerto 80
# -----------------------------------------------------------------------------
class _Pendiente:
    __slots__ = ("sock", "addr", "buf", "t0")

    def __init__(self, sock, addr):
        self.sock, self.addr, self.buf, self.t0 = sock, addr, b"", millis()

    def progreso(self):
        try:
            while True:
                r, _, _ = select.select([self.sock], [], [], 0)
                if not r:
                    return False
                t = self.sock.recv(2048)
                if not t:
                    return None
                self.buf += t
                if b"\r\n\r\n" in self.buf:
                    return True
                if len(self.buf) > 8192:
                    return True
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return None

    def vencido(self):
        return millis() - self.t0 > config.HTTP_ACCEPT_TIMEOUT_MS

    def cerrar(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _cabeceras(buf):
    h = {}
    for linea in buf.split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]:
        if b":" in linea:
            k, _, v = linea.partition(b":")
            h[k.strip().lower().decode("latin-1")] = v.strip().decode("latin-1")
    return h


def _atender(p):
    """Resuelve una conexion del puerto 80: pagina, /status o upgrade a WS."""
    global _ws_sock, _ws_addr, _ws_buf, _mandar_config

    linea = p.buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    cab = _cabeceras(p.buf)
    ruta = linea.split(" ")[1] if len(linea.split(" ")) > 1 else "/"
    es_upgrade = "websocket" in cab.get("upgrade", "").lower()

    if es_upgrade and ruta.split("?")[0] == config.WS_PATH:
        # Un solo espectador, igual que en el modo 1: con dos, el ancho de banda
        # se reparte y las metricas dejan de significar lo que dicen.
        if _ws_sock is not None:
            log_comment("ws: rechazo a %s, ya hay un cliente", p.addr[0])
            p.sock.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                           b"Content-Type: text/plain\r\nConnection: close\r\n\r\n"
                           b"ya hay un cliente conectado al stream\r\n")
            return False

        clave = cab.get("sec-websocket-key", "")
        acepta = base64.b64encode(
            hashlib.sha1(clave.encode() + _GUID).digest()).decode()
        p.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {acepta}\r\n"
                        "\r\n").encode())
        try:
            p.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        p.sock.settimeout(config.HTTP_SEND_TIMEOUT_MS / 1000.0)
        _ws_sock, _ws_addr, _ws_buf = p.sock, p.addr, b""
        _mandar_config = True
        _clock.resync()
        log_comment("ws: cliente %s conectado, handshake 101 completado", p.addr[0])
        return True        # el socket pasa a ser del stream, no se cierra

    if ruta.startswith("/status"):
        cuerpo = metrics_to_json().encode()
        tipo = b"application/json"
    elif ruta.split("?")[0] == "/":
        cuerpo = INDEX_HTML_WS.encode()
        tipo = b"text/html"
    else:
        p.sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n"
                       b"Connection: close\r\n\r\nno existe\r\n")
        return False
    p.sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + tipo +
                   b"\r\nContent-Length: " + str(len(cuerpo)).encode() +
                   b"\r\nConnection: close\r\n\r\n" + cuerpo)
    return False


# -----------------------------------------------------------------------------
# Envio
# -----------------------------------------------------------------------------
def _cerrar_ws(motivo):
    global _ws_sock, _ws_buf
    log_comment("ws: cliente desconectado tras %d frames enviados (%s)",
                g_m.frame_n, motivo)
    try:
        _ws_sock.close()
    except OSError:
        pass
    _ws_sock = None
    _ws_buf = b""


def _leer_del_cliente():
    """Procesa lo que mando el cliente: comandos de texto, close, ping."""
    global _ws_buf
    try:
        r, _, _ = select.select([_ws_sock], [], [], 0)
        if r:
            t = _ws_sock.recv(4096)
            if not t:
                _cerrar_ws("FIN del cliente")
                return
            _ws_buf += t
    except (TimeoutError, socket.timeout):
        pass
    except OSError:
        _cerrar_ws("error de socket")
        return

    while _ws_sock is not None:
        opcode, cuerpo, resto = _leer_frame(_ws_buf)
        if opcode is None:
            break
        _ws_buf = resto
        if opcode == OP_CLOSE:
            _enviar_ws(OP_CLOSE, b"")
            _cerrar_ws("close del cliente")
            return
        if opcode == OP_PING:
            _enviar_ws(OP_PONG, cuerpo)
        elif opcode == OP_TEXT:
            _procesar_comando(cuerpo.decode("utf-8", "replace"))


def _enviar_frame():
    jpeg, capture_ms = _cam.capture()
    if jpeg is None:
        metrics_frame_dropped(DROP_CAPTURE)
        return

    t1 = millis()
    if not _enviar_ws(OP_BINARY, jpeg):
        metrics_frame_dropped(DROP_TRANSPORT)
        _cerrar_ws("escritura fallida")
        return
    send_ms = millis() - t1

    metrics_frame_sent(len(jpeg), capture_ms, send_ms)

    # Misma atribucion de causa que el modo 1: si la escritura tardo mas que una
    # ranura, el culpable es la contrapresion y no el planificador.
    if send_ms > config.FRAME_INTERVAL_MS:
        perdidas = (send_ms // config.FRAME_INTERVAL_MS) - 1
        if perdidas > 0:
            metrics_frame_dropped(DROP_BACKPRESSURE, perdidas)
            _clock.resync()


# -----------------------------------------------------------------------------
# Interfaz de modo
# -----------------------------------------------------------------------------
def mode_name():
    return "MODE_WEBSOCKET (servidor, :%s UI + WS %s)" % (
        _puerto or config.HTTP_UI_PORT, config.WS_PATH)


def mode_setup(cam, ip_local):
    global _srv, _puerto, _cam, _target_fps
    _cam = cam
    _target_fps = config.TARGET_FPS

    from mode_http_mjpeg import _escuchar
    _srv, _puerto = _escuchar(config.HTTP_UI_PORT, config.PORT_FALLBACK_UI,
                              "la UI y el WebSocket")
    _clock.begin(config.FRAME_INTERVAL_MS)

    log_comment("ws: UI en http://%s:%d/", ip_local, _puerto)
    log_comment("ws: stream en ws://%s:%d%s", ip_local, _puerto, config.WS_PATH)
    log_comment("ws: verificar con  python3 tools/verificar_ws.py %s --puerto %d",
                ip_local, _puerto)


def mode_loop():
    global _pend, _mandar_config

    try:
        c, a = _srv.accept()
        c.setblocking(False)
        _pend.append(_Pendiente(c, a))
    except (BlockingIOError, OSError):
        pass

    siguen = []
    for p in _pend:
        listo = p.progreso()
        if listo is None or p.vencido():
            p.cerrar()
            continue
        if listo:
            try:
                p.sock.settimeout(2.0)
                if not _atender(p):
                    p.cerrar()
            except OSError:
                p.cerrar()
            continue
        siguen.append(p)
    _pend = siguen

    if _ws_sock is None:
        _clock.resync()      # sin cliente no se acumulan descartes por ranura
        return

    _leer_del_cliente()
    if _ws_sock is None:
        return

    if _mandar_config and _escribible():
        _mandar_config = False
        _enviar_config()

    if not _clock.due():
        return

    # Equivalente de la trampa del modo asincronico del firmware. Alla la cola
    # de la libreria admite 32 mensajes y con frames VGA son ~1 MB de heap. Aca
    # la regla es la misma: como mucho UN frame en vuelo. Si el socket no acepta
    # datos, el frame nuevo se descarta y se cuenta; un frame viejo no sirve en
    # un stream en vivo.
    if not _escribible():
        metrics_frame_dropped(DROP_BACKPRESSURE)
        return

    _enviar_frame()


def mode_close():
    for p in _pend:
        p.cerrar()
    for s in (_ws_sock, _srv):
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
