"""
mode_mqtt.py  -  Espejo de mode_mqtt.cpp.

MODO 3  -  MQTT. El equipo es CLIENTE.

Diferencia estructural con los modos 1 y 2: NO SE ABRE NINGUN PUERTO. Es la PC
la que sale hacia el broker. En la captura, el SYN va de la PC al broker
(puerto destino 1883), al reves que en los otros dos modos. Esa inversion de la
direccion de la conexion es el punto del modo.

Topicos:
  camara/telemetria  (pub, 1 Hz)  JSON con fps, bytes/s, RSSI, heap, drops...
  camara/cmd         (sub)        "snapshot" | "stream:on" | "stream:off"
  camara/imagen      (pub)        el JPEG crudo

Sin TLS y sin usuario/password a proposito: 1883 en claro para poder leer los
paquetes MQTT en Wireshark (el disector los decodifica solo).

El cliente MQTT 3.1.1 esta escrito a mano, igual que el framing WebSocket del
modo 2 y por el mismo motivo: PubSubClient tambien es minimo, y una libreria
grande agregaria MQTT 5, properties o reconexion propia, que cambiarian los
bytes de la captura respecto del ESP32. Solo se implementa lo que el firmware
usa: CONNECT, SUBSCRIBE, PUBLISH QoS 0, PINGREQ y DISCONNECT.
"""
import socket
import struct

import config
from frame_clock import FrameClock
from metrics import (DROP_CAPTURE, DROP_TRANSPORT, g_m, log_comment,
                     metrics_frame_dropped, metrics_frame_sent,
                     metrics_to_json, millis)

CONNECT, CONNACK = 0x10, 0x20
PUBLISH = 0x30
SUBSCRIBE, SUBACK = 0x82, 0x90
PINGREQ, PINGRESP = 0xC0, 0xD0
DISCONNECT = 0xE0

_sock = None
_buf = b""
_conectado = False
_cam = None
_clock = FrameClock()
_client_id = None

_next_retry_ms = 0
_backoff_ms = config.MQTT_RETRY_BASE_MS
_last_window = 0
_last_in = 0
_last_out = 0

# El callback del firmware solo levanta una bandera porque PubSubClient no es
# reentrante. Aca no haria falta, pero se mantiene la misma forma para que los
# dos codigos se lean igual y el momento del publish sea el mismo.
_snapshot_pedido = False
_streaming = bool(config.MQTT_STREAM_ENABLED)


# -----------------------------------------------------------------------------
# Armado de paquetes MQTT 3.1.1
# -----------------------------------------------------------------------------
def _varint(n):
    """Remaining Length: 7 bits por byte, bit 0x80 de continuacion."""
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


def _paquete_connect():
    # Protocolo "MQTT" nivel 4 (3.1.1), clean session, sin will ni credenciales:
    # exactamente lo que manda PubSubClient con connect(clientId).
    vh = _cadena("MQTT") + bytes([0x04, 0x02]) + struct.pack(">H", config.MQTT_KEEPALIVE_S)
    payload = _cadena(_client_id)
    cuerpo = vh + payload
    return bytes([CONNECT]) + _varint(len(cuerpo)) + cuerpo


def _paquete_subscribe(topico, qos, pid=1):
    cuerpo = struct.pack(">H", pid) + _cadena(topico) + bytes([qos])
    return bytes([SUBSCRIBE]) + _varint(len(cuerpo)) + cuerpo


def _cabecera_publish(topico, nbytes):
    """Solo la cabecera: el payload se escribe aparte.

    Es el equivalente de beginPublish()/write()/endPublish() del firmware, que
    existe para no necesitar un buffer del tamanio del frame. Aca tampoco se
    copia el JPEG a ningun buffer intermedio: cabecera y payload van en dos
    escrituras al socket, igual que en el ESP32."""
    vh = _cadena(topico)                      # QoS 0: sin packet id
    return bytes([PUBLISH]) + _varint(len(vh) + nbytes) + vh


def _enviar(datos):
    global _last_out
    try:
        _sock.sendall(datos)
        _last_out = millis()
        return True
    except (TimeoutError, socket.timeout, OSError):
        return False


# -----------------------------------------------------------------------------
# Conexion, con backoff
# -----------------------------------------------------------------------------
def _desconectar(motivo):
    global _sock, _conectado, _buf
    if _sock is not None:
        try:
            _sock.close()
        except OSError:
            pass
    _sock = None
    _conectado = False
    _buf = b""
    log_comment("mqtt: conexion caida (%s)", motivo)


def _asegurar_conexion():
    global _sock, _conectado, _buf, _next_retry_ms, _backoff_ms, _last_in, _last_out

    if _conectado:
        return

    now = millis()
    if now < _next_retry_ms:
        return                      # todavia no toca

    log_comment("mqtt: conectando a %s:%d (backoff %d ms)",
                config.MQTT_BROKER_IP, config.MQTT_PORT, _backoff_ms)
    try:
        # Igual que setSocketTimeout(3) del firmware: un intento contra un
        # broker caido bloquea como mucho esto, y despues el backoff separa los
        # reintentos. No es del todo no-bloqueante, igual que en el ESP32.
        _sock = socket.create_connection(
            (config.MQTT_BROKER_IP, config.MQTT_PORT),
            timeout=config.MQTT_CONNECT_TIMEOUT_S)
        _sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _sock.sendall(_paquete_connect())

        _sock.settimeout(config.MQTT_CONNECT_TIMEOUT_S)
        resp = _sock.recv(4)
        if len(resp) < 4 or resp[0] != CONNACK:
            raise OSError(f"CONNACK invalido: {resp!r}")
        if resp[3] != 0:
            raise OSError(f"el broker rechazo la conexion, codigo {resp[3]}")

        _sock.sendall(_paquete_subscribe(config.MQTT_TOPIC_CMD, config.MQTT_QOS))
        resp = _sock.recv(5)
        if len(resp) < 4 or resp[0] != SUBACK:
            raise OSError(f"SUBACK invalido: {resp!r}")

        _sock.settimeout(config.HTTP_SEND_TIMEOUT_MS / 1000.0)
        _sock.setblocking(False)
        _conectado = True
        _buf = b""
        _last_in = _last_out = millis()
        _backoff_ms = config.MQTT_RETRY_BASE_MS       # reset del backoff
        _clock.resync()
        log_comment("mqtt: CONNACK ok, suscripto a %s", config.MQTT_TOPIC_CMD)
    except (OSError, socket.timeout) as e:
        if _sock is not None:
            try:
                _sock.close()
            except OSError:
                pass
            _sock = None
        log_comment("mqtt: fallo (%s)", e)
        _backoff_ms = min(config.MQTT_RETRY_MAX_MS, _backoff_ms * 2)
    _next_retry_ms = millis() + _backoff_ms


# -----------------------------------------------------------------------------
# Lectura: comandos entrantes y keepalive
# -----------------------------------------------------------------------------
def _procesar_comando(cmd):
    global _snapshot_pedido, _streaming
    if cmd == "snapshot":
        _snapshot_pedido = True                 # se atiende en el loop
    elif cmd == "stream:on":
        _streaming = True
        _clock.resync()
        log_comment("mqtt: stream continuo ON")
    elif cmd == "stream:off":
        _streaming = False
        log_comment("mqtt: stream continuo OFF")
    else:
        log_comment("mqtt: comando desconocido en %s: \"%s\"",
                    config.MQTT_TOPIC_CMD, cmd[:32])


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
        if mult > 128 ** 3:
            return None, i


def _mqtt_loop():
    """Equivalente de PubSubClient::loop(): procesa entrantes y manda el
    PINGREQ del keepalive."""
    global _buf, _last_in

    try:
        t = _sock.recv(8192)
        if t == b"":
            _desconectar("FIN del broker")
            return
        _buf += t
        _last_in = millis()
    except (BlockingIOError, TimeoutError, socket.timeout):
        pass
    except OSError as e:
        _desconectar(str(e))
        return

    while True:
        if len(_buf) < 2:
            break
        tipo = _buf[0] & 0xF0
        rem, i = _leer_varint(_buf, 1)
        if rem is None or len(_buf) < i + rem:
            break
        cuerpo = _buf[i:i + rem]
        _buf = _buf[i + rem:]

        if tipo == PUBLISH:
            tl = struct.unpack(">H", cuerpo[:2])[0]
            topico = cuerpo[2:2 + tl].decode("utf-8", "replace")
            payload = cuerpo[2 + tl:]
            if (_buf is not None) and topico == config.MQTT_TOPIC_CMD:
                _procesar_comando(payload.decode("utf-8", "replace").strip())
        # PINGRESP y el resto no necesitan accion: alcanza con haber refrescado
        # _last_in al recibirlos.

    # Mismo criterio que PubSubClient: el PINGREQ sale si se quedo viejo
    # CUALQUIERA de los dos lados. Con telemetria a 1 Hz el lado de salida nunca
    # envejece, pero el de entrada si, asi que en la captura aparece un
    # PINGREQ/PINGRESP cada ~15 s.
    now = millis()
    ka = config.MQTT_KEEPALIVE_S * 1000
    if now - _last_in > ka or now - _last_out > ka:
        if not _enviar(bytes([PINGREQ, 0x00])):
            _desconectar("fallo el PINGREQ")


# -----------------------------------------------------------------------------
# Publicacion
# -----------------------------------------------------------------------------
def _publicar_frame(topico, contar_metricas):
    jpeg, capture_ms = _cam.capture()
    if jpeg is None:
        if contar_metricas:
            metrics_frame_dropped(DROP_CAPTURE)
        return False

    # Tope de payload del firmware. En Python no existe el limite de uint16_t de
    # buildHeader(), pero se respeta IGUAL: si los dos lados no descartan en el
    # mismo punto, los descartes dejan de ser comparables.
    if len(jpeg) > config.MQTT_MAX_PAYLOAD_BYTES:
        if contar_metricas:
            metrics_frame_dropped(DROP_TRANSPORT)
        log_comment("mqtt: frame de %d B supera el limite de %d B; "
                    "bajar el framesize o la calidad", len(jpeg),
                    config.MQTT_MAX_PAYLOAD_BYTES)
        return False

    t1 = millis()
    ok = _enviar(_cabecera_publish(topico, len(jpeg))) and _enviar(jpeg)
    send_ms = millis() - t1

    if not ok:
        if contar_metricas:
            metrics_frame_dropped(DROP_TRANSPORT)
        _desconectar("publish incompleto")
        return False

    if contar_metricas:
        metrics_frame_sent(len(jpeg), capture_ms, send_ms)
    return True


def _publicar_telemetria():
    json_txt = metrics_to_json().encode()
    cab = _cabecera_publish(config.MQTT_TOPIC_TELEMETRY, len(json_txt))
    if not _enviar(cab + json_txt):
        log_comment("mqtt: fallo publish de telemetria")
        _desconectar("fallo la telemetria")


# -----------------------------------------------------------------------------
# Interfaz de modo
# -----------------------------------------------------------------------------
def mode_name():
    return "MODE_MQTT (cliente, sale hacia el broker :%d)" % config.MQTT_PORT


def mode_setup(cam, ip_local):
    global _cam, _last_window, _client_id
    _cam = cam
    _clock.begin(config.FRAME_INTERVAL_MS)
    _last_window = g_m.window_seq

    # El firmware usa "xiao-cam" fijo. Aca se agrega el hostname: si dos
    # integrantes apuntan al MISMO broker con el mismo client id, MQTT 3.1.1
    # obliga al broker a patear al primero.
    _client_id = config.MQTT_CLIENT_ID or f"pc-cam-{socket.gethostname().split('.')[0]}"

    log_comment("mqtt: broker %s:%d, sin TLS ni auth (a proposito)",
                config.MQTT_BROKER_IP, config.MQTT_PORT)
    log_comment("mqtt: client id \"%s\"", _client_id)
    log_comment("mqtt: pub %s (1 Hz) / pub %s / sub %s",
                config.MQTT_TOPIC_TELEMETRY, config.MQTT_TOPIC_IMAGE,
                config.MQTT_TOPIC_CMD)
    log_comment("mqtt: stream continuo %s", "ON" if _streaming else "OFF (solo snapshot)")
    log_comment("mqtt: la PC NO escucha en ningun puerto en este modo")


def mode_loop():
    global _last_window, _snapshot_pedido

    _asegurar_conexion()
    if not _conectado:
        _clock.resync()      # sin broker no se acumulan descartes por ranura
        return

    _mqtt_loop()
    if not _conectado:
        return

    # Telemetria: exactamente una por ventana cerrada, sin abrir un timer propio
    # que se desfase del que calcula fps y bytes/s.
    if g_m.window_seq != _last_window:
        _last_window = g_m.window_seq
        _publicar_telemetria()
        if not _conectado:
            return

    # Snapshot bajo demanda. Se publica aca, fuera del parseo del paquete.
    if _snapshot_pedido:
        _snapshot_pedido = False
        log_comment("mqtt: snapshot pedido por %s", config.MQTT_TOPIC_CMD)
        # No cuenta en las metricas de stream: es un evento aparte y
        # contaminaria el fps de la ventana.
        _publicar_frame(config.MQTT_TOPIC_IMAGE, False)
        if not _conectado:
            return

    if not _streaming:
        _clock.resync()
        return

    if _clock.due():
        _publicar_frame(config.MQTT_TOPIC_IMAGE, True)


def mode_close():
    global _sock
    if _conectado and _sock is not None:
        try:
            _sock.setblocking(True)
            _sock.sendall(bytes([DISCONNECT, 0x00]))
        except OSError:
            pass
    if _sock is not None:
        try:
            _sock.close()
        except OSError:
            pass
        _sock = None
