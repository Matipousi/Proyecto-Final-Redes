"""
metrics.py  -  Espejo de metrics.h / metrics.cpp.

Struct de metricas UNICA y compartida por los tres modos. Misma salida que el
firmware, una linea por frame ENVIADO:

    ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped

Todo lo que NO es una fila CSV se emite con prefijo '#', para que el archivo
siga siendo un CSV valido y el MISMO script lea los dos:

    pandas.read_csv("corrida.csv", comment="#")

Diferencias inevitables con el firmware, las dos documentadas en el README:
  heap_free  sale -1: no hay analogo de ESP.getFreeHeap() en una PC.
  rssi       sale -1 salvo que se prenda RSSI_ENABLED en config.py.
"""
import re
import subprocess
import sys
import threading
import time

import config

CSV_HEADER = "ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped"

# Motivos de descarte. Mismos nombres y mismo significado que el enum DropReason
# del firmware: si cambia lo que mide uno, hay que cambiar el otro o se rompe la
# comparacion.
DROP_SCHEDULE     = "sched"      # se paso la ranura: no llegamos al FPS objetivo
DROP_CAPTURE      = "cap"        # la camara no entrego frame
DROP_BACKPRESSURE = "bp"         # el transporte no acepta mas datos
DROP_NO_CLIENT    = "noclient"   # no hay a quien mandarle (informativo)
DROP_TRANSPORT    = "tx"         # fallo el envio (publish rechazado, escritura corta)

_BOOT = time.monotonic()


def millis():
    """Milisegundos desde el arranque, como el millis() de Arduino."""
    return int((time.monotonic() - _BOOT) * 1000)


class Metrics:
    """Equivalente de la struct Metrics del firmware."""

    def __init__(self):
        # Acumulados desde el arranque
        self.frame_n = 0          # frames enviados con exito
        self.dropped = 0          # frames que debian salir y no salieron
        self.bytes_total = 0

        # Desglose de descartes, para explicar el "por que" en el informe
        self.drop_detail = {
            DROP_SCHEDULE: 0,
            DROP_CAPTURE: 0,
            DROP_BACKPRESSURE: 0,
            DROP_NO_CLIENT: 0,
            DROP_TRANSPORT: 0,
        }

        # Ventana de agregacion de 1 s -> lo que se publica como telemetria
        self.win_frames = 0
        self.win_bytes = 0
        self.win_start_ms = 0

        self.fps = 0.0
        self.bytes_per_sec = 0
        # Se incrementa al cerrar cada ventana: permite a un modo publicar
        # telemetria exactamente cuando hay datos nuevos, sin reiniciar la
        # ventana por su lado.
        self.window_seq = 0

        self.boot_ms = 0


g_m = Metrics()

# Donde van las filas de datos y donde los comentarios. Separarlos permite
# `python3 camara_pc.py > corrida.csv` sin perder los avisos de vista.
_csv_out = sys.stdout
_log_out = sys.stderr if config.CSV_SPLIT_STREAMS else sys.stdout


def metrics_csv_a_archivo(ruta):
    """Escribe las filas CSV directo a un archivo en UTF-8.

    Existe por Windows: en PowerShell 5.1 (el que viene con Windows 10/11)
    `python camara_pc.py > corrida.csv` graba el archivo en UTF-16, y pandas
    lo lee como basura. Con --csv el archivo sale bien en cualquier consola."""
    global _csv_out
    _csv_out = open(ruta, "w", encoding="utf-8", newline="")


# -----------------------------------------------------------------------------
# RSSI del WiFi de la PC
# -----------------------------------------------------------------------------
# Se consulta en un HILO APARTE y se deja en una variable. El loop nunca espera
# por esto. La trampa mas cara del firmware fue una instrumentacion que frenaba
# a lo que medal: aca se evita por diseno, y ademas viene apagada por defecto.
_rssi_valor = -1
_rssi_hilo = None


def _leer_rssi_una_vez():
    """dBm, o -1 si no se pudo. Best-effort: las dos plataformas lo exponen de
    forma distinta y ninguna de las dos de forma estable entre versiones."""
    try:
        if sys.platform == "darwin":
            # airport fue removido en macOS reciente y wdutil pide sudo.
            # system_profiler no pide permisos pero es lento (~1-2 s): por eso
            # esto corre en un hilo.
            out = subprocess.run(
                ["system_profiler", "SPAirPortDataType"],
                capture_output=True, text=True, timeout=10).stdout
            for linea in out.splitlines():
                if "Signal / Noise" in linea:
                    # "Signal / Noise: -43 dBm / -90 dBm"
                    return int(linea.split(":")[1].strip().split()[0])
        elif sys.platform.startswith("win"):
            out = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, errors="replace",
                timeout=10).stdout
            # "Signal : 85%" en ingles, "Senal : 85%" en castellano (con enie,
            # que segun la pagina de codigos de la consola puede llegar
            # cualquier cosa: por eso Se.al y no el texto literal).
            for linea in out.splitlines():
                m = re.match(r"\s*(Signal|Se.{1,2}al)\s*:\s*(\d+)\s*%", linea)
                if m:
                    pct = int(m.group(2))
                    # netsh da porcentaje de calidad, no dBm. Conversion
                    # estandar y aproximada; sirve para ver tendencia, no como
                    # medicion absoluta. En el README esta aclarado.
                    return (pct // 2) - 100
    except Exception:
        pass
    return -1


def _rssi_loop():
    global _rssi_valor
    while True:
        _rssi_valor = _leer_rssi_una_vez()
        time.sleep(config.RSSI_REFRESH_MS / 1000.0)


def rssi():
    return _rssi_valor


# -----------------------------------------------------------------------------
# API, con los mismos nombres que en el firmware
# -----------------------------------------------------------------------------
def log_comment(fmt, *args):
    """Log fuera de banda: siempre con '#' adelante para no ensuciar el CSV."""
    texto = fmt % args if args else fmt
    print(f"# {texto}", file=_log_out, flush=True)


def metrics_begin():
    global _rssi_hilo
    g_m.__init__()
    g_m.boot_ms = millis()
    g_m.win_start_ms = g_m.boot_ms

    if config.RSSI_ENABLED and _rssi_hilo is None:
        _rssi_hilo = threading.Thread(target=_rssi_loop, daemon=True)
        _rssi_hilo.start()

    if config.CSV_ENABLED:
        print(CSV_HEADER, file=_csv_out, flush=True)


def metrics_frame_sent(nbytes, capture_ms, send_ms):
    """Registra un frame efectivamente puesto en la red. Imprime la fila CSV."""
    g_m.frame_n += 1
    g_m.bytes_total += nbytes
    g_m.win_frames += 1
    g_m.win_bytes += nbytes

    if config.CSV_ENABLED:
        print(f"{millis()},{g_m.frame_n},{nbytes},{capture_ms},{send_ms},"
              f"{config.HEAP_FREE_PLACEHOLDER},{rssi()},{g_m.dropped}",
              file=_csv_out, flush=True)


def metrics_frame_dropped(reason, count=1):
    """Registra frames que no salieron. NO imprime fila: 'dropped' es una
    COLUMNA acumulada, no un tipo de fila. La fila siguiente muestra el
    contador ya subido."""
    g_m.dropped += count
    g_m.drop_detail[reason] += count


def metrics_tick():
    """Cierra la ventana de 1 s y recalcula fps / bytes_per_sec.
    Devuelve True en el tick en que la ventana se cerro."""
    now = millis()
    elapsed = now - g_m.win_start_ms
    if elapsed < config.TELEMETRY_INTERVAL_MS:
        return False

    g_m.fps = (g_m.win_frames * 1000.0) / elapsed
    g_m.bytes_per_sec = int(g_m.win_bytes * 1000 // elapsed)

    g_m.win_frames = 0
    g_m.win_bytes = 0
    g_m.win_start_ms = now
    g_m.window_seq += 1
    return True


def metrics_to_json():
    """Telemetria en JSON. MISMOS campos, mismas unidades y mismo orden que
    metricsToJson() del firmware, para poder cruzar los dos sin convertir.

    El fps se arma con aritmetica entera (un decimal) igual que el firmware,
    que lo hace asi para no depender del printf de floats."""
    fps_x10 = int(g_m.fps * 10.0 + 0.5)
    return (
        '{"fps":%d.%d,"bytes_s":%d,"rssi":%d,"heap_free":%d,'
        '"dropped":%d,"frames":%d,"uptime_s":%d,'
        '"drop_detail":{"sched":%d,"cap":%d,"bp":%d,"noclient":%d,"tx":%d}}'
    ) % (
        fps_x10 // 10, fps_x10 % 10,
        g_m.bytes_per_sec,
        rssi(),
        config.HEAP_FREE_PLACEHOLDER,
        g_m.dropped,
        g_m.frame_n,
        (millis() - g_m.boot_ms) // 1000,
        g_m.drop_detail[DROP_SCHEDULE],
        g_m.drop_detail[DROP_CAPTURE],
        g_m.drop_detail[DROP_BACKPRESSURE],
        g_m.drop_detail[DROP_NO_CLIENT],
        g_m.drop_detail[DROP_TRANSPORT],
    )
