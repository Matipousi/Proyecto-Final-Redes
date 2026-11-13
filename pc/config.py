"""
config.py  -  Toda la configuracion del port a PC en un solo lugar.

Espejo de config.h. Mismo criterio que el firmware: el programa es el
instrumento, no el entregable. Ante la duda entre rendimiento y
observabilidad, gana observabilidad.

Para que las capturas sean comparables con las del ESP32, lo que tiene que
coincidir es: TARGET_FPS, el tamanio de frame, el bitrate resultante y los
puertos. La calidad JPEG NO se puede igualar por numero (son dos codificadores
distintos): se iguala por BYTES, con `python3 camara_pc.py --calibrar`.
"""

# -----------------------------------------------------------------------------
# 1. SELECTOR DE MODO
# -----------------------------------------------------------------------------
MODE_HTTP_MJPEG = 1   # el equipo es SERVIDOR : HTTP multipart, puerto 81
MODE_WEBSOCKET  = 2   # el equipo es SERVIDOR : WebSocket binario, puerto 80
MODE_MQTT       = 3   # el equipo es CLIENTE  : sale hacia el broker, 1883

# >>> CAMBIAR ESTA LINEA PARA ELEGIR EL MODO <<<
# Se puede sobreescribir sin tocar el archivo con `--modo N`, igual que
# -DSTREAM_MODE=n en PlatformIO.
STREAM_MODE = MODE_HTTP_MJPEG

# -----------------------------------------------------------------------------
# 2. RED
# -----------------------------------------------------------------------------
# No hay seccion de WiFi: la PC ya esta asociada y la maneja el sistema
# operativo. Es la primera diferencia estructural con el firmware, y es la que
# hace que en el port no exista net.py.
#
# A que interfaz atarse en los modos 1 y 2.
#   "0.0.0.0"   todas: desde otra maquina de la LAN se llega. Es lo que hay que
#               usar para medir (ver "Trampa del loopback" en el README).
#   "127.0.0.1" solo local: la captura sale por loopback y NO es comparable.
BIND_HOST = "0.0.0.0"

# -----------------------------------------------------------------------------
# 3. BROKER MQTT  (solo MODE_MQTT)
# -----------------------------------------------------------------------------
MQTT_BROKER_IP = "127.0.0.1"   # IP del Mosquitto. Ver nota sobre loopback.
MQTT_PORT      = 1883          # texto plano: es el punto del trabajo

# El firmware usa "xiao-cam" fijo. Aca se le agrega el nombre de la maquina
# porque si dos integrantes del equipo apuntan al MISMO broker con el mismo
# client id, el broker patea al primero (lo manda el protocolo MQTT 3.1.1).
MQTT_CLIENT_ID = None          # None = "pc-cam-<hostname>"

MQTT_TOPIC_TELEMETRY = "camara/telemetria"
MQTT_TOPIC_CMD       = "camara/cmd"
MQTT_TOPIC_IMAGE     = "camara/imagen"

MQTT_QOS            = 0        # QoS 0: 1 PUBLISH, sin PUBACK
MQTT_KEEPALIVE_S    = 15       # PINGREQ/PINGRESP visibles en la captura
MQTT_CONNECT_TIMEOUT_S = 3     # igual que setSocketTimeout(3) del firmware

# Tope de payload del firmware. En Python no existe el limite de uint16_t de
# PubSubClient, pero se respeta IGUAL: si los dos lados no descartan en el mismo
# punto, los descartes dejan de ser comparables. Es paridad a proposito.
MQTT_MAX_PAYLOAD_BYTES = 65000

MQTT_RETRY_BASE_MS = 1000
MQTT_RETRY_MAX_MS  = 30000

# 0 = solo telemetria 1 Hz + snapshot bajo demanda (default del firmware)
# 1 = ademas publica frames a TARGET_FPS. Tambien se prende en caliente con
#     "stream:on" en camara/cmd.
MQTT_STREAM_ENABLED = 0

# -----------------------------------------------------------------------------
# 4. CAMARA  (webcam de la PC, en lugar de la OV2640)
# -----------------------------------------------------------------------------
# Nombres y medidas iguales a los framesize_t del firmware.
FRAMESIZE = {
    "QVGA": (320, 240),
    "VGA":  (640, 480),
    "SVGA": (800, 600),
    "HD":   (1280, 720),
    "UXGA": (1600, 1200),
}
CAM_FRAMESIZE = "VGA"

# Calidad JPEG de OpenCV: 0..100, MAYOR = mejor calidad y mas bytes.
# OJO: es la escala INVERSA a CAM_JPEG_QUALITY del firmware (10..63, menor =
# mejor). No se corresponden por numero. El firmware medido en VGA q12 da
# 17-23 KB por frame; con `--calibrar` se busca el valor de aca que caiga en esa
# misma franja, y ahi las capturas pasan a ser comparables en bitrate.
CAM_JPEG_QUALITY_CV = 70

# Indice de la camara para OpenCV. 0 = la de siempre. Si hay varias (notebook +
# USB), probar 1, 2...
CAM_INDEX = 0

# "webcam"    camara real (necesita opencv-python)
# "sintetica" patron generado, sin camara. Sirve para probar el transporte en
#             una maquina sin webcam, o con la webcam ocupada.
CAM_SOURCE = "webcam"

# -----------------------------------------------------------------------------
# 5. RITMO DEL STREAM
# -----------------------------------------------------------------------------
# FPS objetivo FIJO, no "lo mas rapido posible": es lo que hace comparables las
# capturas entre si y con las del ESP32. Un frame que no sale en su ranura
# cuenta como dropped.
TARGET_FPS = 10
FRAME_INTERVAL_MS = 1000 // TARGET_FPS

# -----------------------------------------------------------------------------
# 6. PUERTOS
# -----------------------------------------------------------------------------
HTTP_SEND_TIMEOUT_MS     = 2000   # tope de una escritura antes de darla por corta
HTTP_SLOW_CLIENT_WARN_MS = 500    # desde cuanto se avisa que el cliente es lento
HTTP_ACCEPT_TIMEOUT_MS   = 1000   # presupuesto para que un cliente nuevo hable

HTTP_UI_PORT     = 80   # pagina de control (modos 1 y 2)
HTTP_STREAM_PORT = 81   # stream MJPEG (modo 1), separado a proposito
WS_PATH          = "/stream"

# En Linux los puertos < 1024 necesitan root; en Windows no; en macOS 10.14+
# no, salvo que BIND_HOST sea una IP concreta en vez de 0.0.0.0. Con esto en True, si no se
# puede atar el 80 se usa PORT_FALLBACK_UI y se avisa, en lugar de morir.
# OJO: cambiar el puerto cambia los filtros de Wireshark. Para la corrida de
# medicion conviene usar el 80 de verdad (ver README).
PORT_FALLBACK_ENABLED = True
PORT_FALLBACK_UI      = 8080
PORT_FALLBACK_STREAM   = 8081

# -----------------------------------------------------------------------------
# 7. INSTRUMENTACION
# -----------------------------------------------------------------------------
CSV_ENABLED           = True   # 1 linea CSV por frame enviado
TELEMETRY_INTERVAL_MS = 1000   # ventana de agregacion (fps, bytes/s)

# El CSV sale por stdout; los comentarios '#' y los errores, por stderr. Asi
# `python3 camara_pc.py > corrida.csv` deja un CSV limpio y los avisos siguen
# apareciendo en la terminal. Con esto en False todo va junto a stdout, como en
# el puerto serie del firmware.
CSV_SPLIT_STREAMS = True

# RSSI real del WiFi de la PC. APAGADO por defecto a proposito: averiguarlo
# obliga a lanzar un proceso del sistema, y la trampa mas cara del firmware fue
# justamente una instrumentacion que frenaba a lo que media. Con esto en True se
# consulta en un hilo aparte cada RSSI_REFRESH_MS y nunca bloquea el loop.
# Apagado, la columna rssi del CSV sale -1.
RSSI_ENABLED    = False
RSSI_REFRESH_MS = 5000

# La columna heap_free del CSV no tiene analogo en una PC: siempre sale -1. Se
# mantiene igual para que el MISMO script de pandas lea los dos CSV.
HEAP_FREE_PLACEHOLDER = -1

# -----------------------------------------------------------------------------
# 8. TLS / AUTENTICACION  -  DESHABILITADO A PROPOSITO EN ESTA ETAPA
# -----------------------------------------------------------------------------
# Igual que en el firmware: la captura tiene que salir en texto plano. Los
# ganchos quedan preparados para la etapa siguiente.
#
# USE_TLS = True
# MQTT_PORT_TLS = 8883
# MQTT_CA_CERT = "/ruta/ca.crt"
# MQTT_USER = "usuario"
# MQTT_PASS = "password"
# HTTP_AUTH_USER = "admin"
# HTTP_AUTH_PASS = "admin"

# -----------------------------------------------------------------------------
# Chequeos de coherencia
# -----------------------------------------------------------------------------
assert STREAM_MODE in (MODE_HTTP_MJPEG, MODE_WEBSOCKET, MODE_MQTT), \
    "STREAM_MODE invalido: usar MODE_HTTP_MJPEG, MODE_WEBSOCKET o MODE_MQTT"
assert TARGET_FPS >= 1, "TARGET_FPS debe ser >= 1"
assert CAM_FRAMESIZE in FRAMESIZE, f"CAM_FRAMESIZE invalido: {CAM_FRAMESIZE}"
assert 0 <= CAM_JPEG_QUALITY_CV <= 100, "CAM_JPEG_QUALITY_CV va de 0 a 100"
assert CAM_SOURCE in ("webcam", "sintetica"), "CAM_SOURCE: webcam o sintetica"
