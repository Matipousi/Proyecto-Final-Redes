"""
mode_http_mjpeg.py  -  Espejo de mode_http_mjpeg.cpp.

MODO 1  -  HTTP MJPEG. El equipo es SERVIDOR.

  Puerto 80 : pagina de control.
  Puerto 81 : el stream.

Los dos puertos estan separados a proposito: en la captura se ve al navegador
abrir una conexion corta al 80 para bajar el HTML y otra al 81 que ya no cierra.
Con un solo puerto se confundirian.

El stream es un unico cuerpo HTTP infinito:
  Content-Type: multipart/x-mixed-replace; boundary=frameboundary
y dentro, un JPEG por parte. La respuesta nunca termina, y por eso la conexion
TCP queda abierta: no hay un request por frame.

Los bytes son los MISMOS que emite el firmware, incluida la forma con el CRLF
adelante del boundary y las dos escrituras por frame (ver trampa 11 del
HANDOFF). Por eso tools/verificar_mjpeg.py sirve igual contra el ESP32 y contra
esto, sin cambiar nada.
"""
import select
import socket
import sys

import config
from camera import camera_begin
from frame_clock import FrameClock
from metrics import (DROP_BACKPRESSURE, DROP_CAPTURE, g_m, log_comment,
                     metrics_frame_dropped, metrics_frame_sent,
                     metrics_to_json, millis)
from pages import pagina_mjpeg

PART_BOUNDARY = b"frameboundary"

_srv_ui = None
_srv_stream = None
_puerto_ui = None
_puerto_stream = None

_pend_ui = []          # clientes del 80 que todavia no completaron su request
_pend_stream = None    # el del 81 (uno solo, igual que el firmware)

_stream_sock = None
# Flag explicito de "hay stream abierto", igual que s_stream_active en el
# firmware. Alla hace falta porque el operator bool() de WiFiClient ES
# connected(); aca se mantiene para que los dos codigos se lean igual.
_stream_active = False

_clock = FrameClock()
_cam = None


# -----------------------------------------------------------------------------
# Lectura NO bloqueante de la request
# -----------------------------------------------------------------------------
# Trampa 3 del firmware: leer la request con una lectura bloqueante costaba
# hasta 1 s por conexion y se comia ranuras en silencio. Aca igual: se consume
# lo que YA llego y se vuelve; HTTP_ACCEPT_TIMEOUT_MS es un presupuesto de
# reloj de pared, no una espera.
class _Pendiente:
    __slots__ = ("sock", "addr", "buf", "t0")

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.buf = b""
        self.t0 = millis()

    def progreso(self):
        """True cuando la request esta completa. None si el cliente se fue."""
        try:
            while True:
                r, _, _ = select.select([self.sock], [], [], 0)
                if not r:
                    return False
                trozo = self.sock.recv(2048)
                if not trozo:
                    return None            # cerro antes de terminar
                self.buf += trozo
                if b"\r\n\r\n" in self.buf:
                    return True
                if len(self.buf) > 8192:
                    return True            # tope: no confiar en el cliente
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return None

    def vencido(self):
        return millis() - self.t0 > config.HTTP_ACCEPT_TIMEOUT_MS

    def linea(self):
        return self.buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")

    def cerrar(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _escuchar(puerto, puerto_alt, que):
    """Devuelve (socket, puerto_real). Cae al alternativo si el privilegiado no
    se puede atar (en macOS y Linux los puertos < 1024 piden root)."""
    for p in (puerto, puerto_alt):
        if p is None:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform.startswith("win"):
                # En Windows SO_REUSEADDR significa otra cosa: deja que un
                # SEGUNDO proceso ate el mismo puerto y le robe conexiones al
                # primero. Con dos corridas abiertas sin querer, el navegador
                # caeria en una cualquiera. EXCLUSIVEADDRUSE lo impide.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((config.BIND_HOST, p))
            s.listen(4)
            s.setblocking(False)
            if p != puerto:
                log_comment("aviso: no pude atar el puerto %d para %s, uso el %d",
                            puerto, que, p)
                log_comment("aviso: OJO, cambia los filtros de Wireshark. "
                            "Para medir conviene 'sudo' y el puerto real")
            return s, p
        except PermissionError:
            if not config.PORT_FALLBACK_ENABLED:
                raise
            continue
        except OSError as e:
            if p == puerto and config.PORT_FALLBACK_ENABLED:
                log_comment("aviso: puerto %d ocupado o no disponible (%s)", p, e)
                continue
            raise
    raise OSError(f"no pude escuchar en {puerto} ni en {puerto_alt}")


def _nodelay(s):
    # Nagle OFF: queremos que cada write() salga al cable cuando lo pedimos. Con
    # Nagle activo el stack junta escrituras y la temporizacion de los paquetes
    # dejaria de reflejar el ritmo del emisor.
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


# -----------------------------------------------------------------------------
# Puerto 80: pagina de control y /status
# -----------------------------------------------------------------------------
def _responder_ui(p):
    linea = p.linea()
    if linea.startswith("GET /status"):
        cuerpo = metrics_to_json().encode()
        tipo = b"application/json"
    elif linea.startswith("GET / ") or linea.startswith("GET /?"):
        cuerpo = pagina_mjpeg(_puerto_stream).encode()
        tipo = b"text/html"
    else:
        p.sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n"
                       b"Connection: close\r\n\r\nno existe\r\n")
        return
    p.sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + tipo +
                   b"\r\nContent-Length: " + str(len(cuerpo)).encode() +
                   b"\r\nConnection: close\r\n\r\n" + cuerpo)


# -----------------------------------------------------------------------------
# Puerto 81: aceptar al cliente del stream
# -----------------------------------------------------------------------------
_CAB_STREAM = (b"HTTP/1.1 200 OK\r\n"
               b"Content-Type: multipart/x-mixed-replace; boundary=" + PART_BOUNDARY + b"\r\n"
               b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
               b"Pragma: no-cache\r\n"
               b"Access-Control-Allow-Origin: *\r\n"
               b"Connection: close\r\n"
               b"\r\n")


def _aceptar_stream():
    global _pend_stream, _stream_sock, _stream_active

    try:
        nc, addr = _srv_stream.accept()
    except (BlockingIOError, OSError):
        nc = None
    if nc is not None:
        nc.setblocking(False)
        # Un solo espectador por vez. Con dos, el ancho de banda se reparte y
        # las metricas dejan de significar lo que dicen: mejor rechazar
        # explicito y que se vea el 503 en la captura, antes que degradar la
        # medicion en silencio.
        if _stream_active or _pend_stream is not None:
            try:
                nc.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                           b"Content-Type: text/plain\r\n"
                           b"Connection: close\r\n\r\n"
                           b"ya hay un cliente conectado al stream\r\n")
            except OSError:
                pass
            log_comment("http: 503 a %s (stream ya ocupado)", addr[0])
            nc.close()
        else:
            _pend_stream = _Pendiente(nc, addr)

    if _pend_stream is None:
        return

    listo = _pend_stream.progreso()
    if listo is None:
        _pend_stream.cerrar()
        _pend_stream = None
        return
    if not listo:
        if _pend_stream.vencido():
            log_comment("http: %s conecto al :%d y no mando request, lo suelto",
                        _pend_stream.addr[0], _puerto_stream)
            _pend_stream.cerrar()
            _pend_stream = None
        return

    linea = _pend_stream.linea()
    if not (linea.startswith("GET /stream") or linea.startswith("GET / ")):
        try:
            _pend_stream.sock.sendall(b"HTTP/1.1 404 Not Found\r\n"
                                      b"Content-Type: text/plain\r\n"
                                      b"Connection: close\r\n\r\n"
                                      b"usar GET /stream\r\n")
        except OSError:
            pass
        _pend_stream.cerrar()
        _pend_stream = None
        return

    s = _pend_stream.sock
    _nodelay(s)
    # Un cliente que deja de leer llena la ventana TCP y la escritura se queda
    # esperando. Con esto el bloqueo queda acotado: al vencer, la escritura sale
    # corta, se detecta en _enviar_frame y se cierra el stream en vez de
    # arrastrar el loop.
    s.settimeout(config.HTTP_SEND_TIMEOUT_MS / 1000.0)

    try:
        s.sendall(_CAB_STREAM)
    except OSError:
        _pend_stream.cerrar()
        _pend_stream = None
        return

    _stream_sock = s
    _stream_active = True
    log_comment("http: cliente de stream %s conectado, multipart abierto",
                _pend_stream.addr[0])
    _pend_stream = None
    _clock.resync()   # el reloj arranca recien cuando hay a quien enviarle


def _stream_sigue_vivo():
    """El cliente del stream no manda nada: si hay algo para leer es un FIN."""
    try:
        r, _, _ = select.select([_stream_sock], [], [], 0)
        if r and _stream_sock.recv(1, socket.MSG_PEEK) == b"":
            return False
    except OSError:
        return False
    return True


# -----------------------------------------------------------------------------
# Envio de un frame como una parte del multipart
# -----------------------------------------------------------------------------
def _enviar_frame():
    global _stream_sock, _stream_active

    jpeg, capture_ms = _cam.capture()
    if jpeg is None:
        metrics_frame_dropped(DROP_CAPTURE)
        return

    # Cabecera de la parte. X-Slot / X-Frame-N / X-Timestamp-Ms no son estandar:
    # van para poder cruzar cada parte de la captura con su fila del CSV.
    #   X-Slot    numero de ranura del reloj de FPS -> un SALTO aca es un frame
    #             descartado, visible en Wireshark sin abrir el CSV.
    #   X-Frame-N numero de frame efectivamente enviado (nunca deja hueco).
    #
    # El CRLF va ADELANTE del boundary, no al final de la parte anterior: asi
    # son 2 escrituras por frame en vez de 3 (la tercera era un paquete de 2
    # bytes). Es la forma que usa el CameraWebServer oficial de Espressif y la
    # que usa el firmware. No "arreglar".
    cab = (b"\r\n--" + PART_BOUNDARY + b"\r\n"
           b"Content-Type: image/jpeg\r\n"
           b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n"
           b"X-Slot: " + str(_clock.slot()).encode() + b"\r\n"
           b"X-Frame-N: " + str(g_m.frame_n + 1).encode() + b"\r\n"
           b"X-Timestamp-Ms: " + str(millis()).encode() + b"\r\n"
           b"\r\n")

    t1 = millis()
    try:
        _stream_sock.sendall(cab)
        _stream_sock.sendall(jpeg)
    except (TimeoutError, socket.timeout, OSError) as e:
        # Escritura corta: el socket se lleno o el cliente se fue a mitad de
        # camino. La parte quedo truncada, asi que la conexion ya no es
        # recuperable.
        send_ms = millis() - t1
        metrics_frame_dropped(DROP_BACKPRESSURE)
        log_comment("http: escritura corta tras %d ms (%s), cierro el stream",
                    send_ms, type(e).__name__)
        try:
            _stream_sock.close()
        except OSError:
            pass
        _stream_sock = None
        _stream_active = False
        return
    send_ms = millis() - t1

    metrics_frame_sent(len(jpeg), capture_ms, send_ms)

    # Atribucion de la causa. Si la escritura tardo MAS que una ranura, no es
    # que el planificador llegara tarde: es que el consumidor no drena y TCP nos
    # frena a nosotros. Sin esto esas ranuras caerian en 'sched' y el desglose
    # por causa diria cualquier cosa.
    #
    # Que MJPEG sobre HTTP acople el ritmo del emisor al del receptor NO es un
    # bug: es la propiedad del transporte que este trabajo justamente compara.
    # Por eso se mide y se reporta, no se disimula.
    if send_ms > config.FRAME_INTERVAL_MS:
        perdidas = (send_ms // config.FRAME_INTERVAL_MS) - 1
        if perdidas > 0:
            metrics_frame_dropped(DROP_BACKPRESSURE, perdidas)
            _clock.resync()   # que due() no las cuente de nuevo como 'sched'
        if send_ms >= config.HTTP_SLOW_CLIENT_WARN_MS:
            log_comment("http: cliente lento, envio de %d ms (%d ranuras perdidas)",
                        send_ms, perdidas)


# -----------------------------------------------------------------------------
# Interfaz de modo  (espejo de streamModeSetup / Loop / Name)
# -----------------------------------------------------------------------------
def mode_name():
    return "MODE_HTTP_MJPEG (servidor, :%s UI + :%s stream)" % (
        _puerto_ui or config.HTTP_UI_PORT, _puerto_stream or config.HTTP_STREAM_PORT)


def mode_setup(cam, ip_local):
    global _srv_ui, _srv_stream, _puerto_ui, _puerto_stream, _cam
    _cam = cam

    _srv_ui, _puerto_ui = _escuchar(
        config.HTTP_UI_PORT, config.PORT_FALLBACK_UI, "la UI")
    _srv_stream, _puerto_stream = _escuchar(
        config.HTTP_STREAM_PORT, config.PORT_FALLBACK_STREAM, "el stream")

    _clock.begin(config.FRAME_INTERVAL_MS)

    log_comment("http: UI en http://%s:%d/", ip_local, _puerto_ui)
    log_comment("http: stream en http://%s:%d/stream", ip_local, _puerto_stream)
    log_comment("http: verificar con  python3 tools/verificar_mjpeg.py %s --puerto %d",
                ip_local, _puerto_stream)


def mode_loop():
    global _pend_ui, _stream_sock, _stream_active

    # Puerto 80
    try:
        c, a = _srv_ui.accept()
        c.setblocking(False)
        _pend_ui.append(_Pendiente(c, a))
    except (BlockingIOError, OSError):
        pass

    siguen = []
    for p in _pend_ui:
        listo = p.progreso()
        if listo is None or p.vencido():
            p.cerrar()
            continue
        if listo:
            try:
                p.sock.settimeout(2.0)
                _responder_ui(p)
            except OSError:
                pass
            p.cerrar()
            continue
        siguen.append(p)
    _pend_ui = siguen

    # Puerto 81
    _aceptar_stream()

    if _stream_active and not _stream_sigue_vivo():
        log_comment("http: cliente de stream desconectado tras %d frames enviados",
                    g_m.frame_n)
        try:
            _stream_sock.close()
        except OSError:
            pass
        _stream_sock = None
        _stream_active = False

    if not _stream_active:
        _clock.resync()   # sin cliente no se acumulan descartes por ranura
        return

    if _clock.due():
        _enviar_frame()


def mode_close():
    for p in _pend_ui:
        p.cerrar()
    if _pend_stream is not None:
        _pend_stream.cerrar()
    for s in (_stream_sock, _srv_ui, _srv_stream):
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
