#!/usr/bin/env python3
"""
camara_pc.py  -  Espejo de camara_redes.ino

Maqueta de medicion para Redes de Datos, version PC. El mismo stream de video
sobre tres arquitecturas de transporte, con los MISMOS protocolos, los mismos
puertos y la misma instrumentacion que el firmware del XIAO ESP32S3:

  1) MODE_HTTP_MJPEG  el equipo es SERVIDOR : HTTP multipart en el puerto 81
  2) MODE_WEBSOCKET   el equipo es SERVIDOR : WebSocket binario en el 80
  3) MODE_MQTT        el equipo es CLIENTE  : sale hacia el broker, 1883

Sirve para que todo el equipo pueda trabajar con una sola placa: cada uno corre
esto en su PC con su webcam y captura su propio trafico.

Sin TLS y sin autenticacion a proposito en esta etapa: la captura tiene que
poder leerse en claro.

    python3 camara_pc.py --modo 1
    python3 camara_pc.py --modo 3 --broker 192.168.4.58
    python3 camara_pc.py --fuente sintetica          # sin webcam
    python3 camara_pc.py --calibrar                  # igualar bytes con el ESP32

LEER EL README ANTES DE MEDIR: si el emisor y el receptor son la misma maquina,
el trafico sale por loopback y la segmentacion TCP no es comparable con la del
ESP32 por WiFi.
"""
import argparse
import socket
import sys
import time

import config


def ip_local():
    """IP de la interfaz con la que la maquina sale a la red. No manda nada:
    un socket UDP conectado solo elige la ruta."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def parsear_args():
    p = argparse.ArgumentParser(
        description="Port a PC de camara_redes: tres transportes, misma instrumentacion.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modo", type=int, choices=(1, 2, 3),
                   help="1 HTTP MJPEG, 2 WebSocket, 3 MQTT (default: config.py)")
    p.add_argument("--fuente", choices=("webcam", "sintetica"),
                   help="de donde salen los frames")
    p.add_argument("--fps", type=int, help="FPS objetivo")
    p.add_argument("--framesize", choices=tuple(config.FRAMESIZE),
                   help="tamanio de frame")
    p.add_argument("--calidad", type=int, metavar="0-100",
                   help="calidad JPEG de OpenCV (escala INVERSA a la del firmware)")
    p.add_argument("--camara", type=int, metavar="N", help="indice de webcam")
    p.add_argument("--broker", help="IP del broker MQTT (modo 3)")
    p.add_argument("--bind", help="interfaz donde escuchar (modos 1 y 2)")
    p.add_argument("--csv", metavar="ARCHIVO",
                   help="grabar las filas CSV en este archivo (usar en Windows "
                        "en lugar de '>': PowerShell lo graba en UTF-16)")
    p.add_argument("--duracion", type=float, metavar="SEG",
                   help="corta solo tras N segundos (para corridas repetibles)")
    p.add_argument("--calibrar", action="store_true",
                   help="mide el tamanio del JPEG a varias calidades y recomienda "
                        "la que iguala al ESP32")
    return p.parse_args()


def aplicar_args(a):
    if a.modo:       config.STREAM_MODE = a.modo
    if a.fuente:     config.CAM_SOURCE = a.fuente
    if a.framesize:  config.CAM_FRAMESIZE = a.framesize
    if a.calidad is not None: config.CAM_JPEG_QUALITY_CV = a.calidad
    if a.camara is not None:  config.CAM_INDEX = a.camara
    if a.broker:     config.MQTT_BROKER_IP = a.broker
    if a.bind:       config.BIND_HOST = a.bind
    if a.fps:
        config.TARGET_FPS = a.fps
        config.FRAME_INTERVAL_MS = max(1, 1000 // a.fps)

    assert config.STREAM_MODE in (1, 2, 3)
    assert config.TARGET_FPS >= 1, "TARGET_FPS debe ser >= 1"
    assert 0 <= config.CAM_JPEG_QUALITY_CV <= 100, "la calidad va de 0 a 100"


# -----------------------------------------------------------------------------
# Calibracion: igualar BYTES con el ESP32, no numeros de calidad
# -----------------------------------------------------------------------------
# El firmware medido en VGA q12 da 17-23 KB por frame (~20 KB de media). Los dos
# codificadores JPEG son distintos y las escalas de calidad son inversas, asi
# que la unica forma honesta de hacer comparables los bitrates es medir.
OBJETIVO_BYTES_VGA = 20000


def calibrar():
    from camera import camera_begin, camera_log_config
    from metrics import log_comment, metrics_begin

    metrics_begin()
    cam = camera_begin()
    if cam is None:
        return 1
    camera_log_config(cam)

    w, h = config.FRAMESIZE[config.CAM_FRAMESIZE]
    objetivo = int(OBJETIVO_BYTES_VGA * (w * h) / (640 * 480))

    print(f"\nobjetivo: ~{objetivo} B por frame "
          f"(ESP32 en VGA q12 = 17-23 KB, escalado a {config.CAM_FRAMESIZE})\n")
    print(f"{'calidad':>8}  {'media':>8}  {'min':>8}  {'max':>8}  {'kbit/s':>8}")
    print("-" * 48)

    mejor, mejor_dif = None, None
    for q in (40, 50, 55, 60, 65, 70, 75, 80, 85, 90):
        cam.calidad = q
        tam = []
        for _ in range(12):
            jpeg, _ms = cam.capture()
            if jpeg:
                tam.append(len(jpeg))
            time.sleep(0.03)
        if not tam:
            continue
        media = sum(tam) // len(tam)
        kbits = media * config.TARGET_FPS * 8 / 1000
        marca = ""
        dif = abs(media - objetivo)
        if mejor_dif is None or dif < mejor_dif:
            mejor, mejor_dif = q, dif
        print(f"{q:>8}  {media:>8}  {min(tam):>8}  {max(tam):>8}  {kbits:>8.0f}{marca}")

    cam.close()
    if mejor is not None:
        print(f"\nmas cercano al ESP32: calidad {mejor}")
        print(f"poner en config.py:  CAM_JPEG_QUALITY_CV = {mejor}")
        print(f"o correr con:        --calidad {mejor}")
    if config.CAM_SOURCE == "sintetica":
        print("\nOJO: con la fuente sintetica esto no calibra nada util; "
              "la calibracion es para la webcam real.")
    return 0


# -----------------------------------------------------------------------------
# setup() / loop()
# -----------------------------------------------------------------------------
def main():
    a = parsear_args()
    aplicar_args(a)

    if a.calibrar:
        return calibrar()

    # Import diferido: cada modo importa solo lo suyo, igual que en el firmware
    # cada mode_*.cpp esta dentro de su #if y los otros no arrastran nada. Asi
    # el modo 1 no necesita nada instalado.
    if config.STREAM_MODE == config.MODE_HTTP_MJPEG:
        import mode_http_mjpeg as modo
    elif config.STREAM_MODE == config.MODE_WEBSOCKET:
        import mode_websocket as modo
    else:
        import mode_mqtt as modo

    from camera import camera_begin, camera_log_config
    from metrics import (log_comment, metrics_begin, metrics_tick, millis,
                         metrics_csv_a_archivo)
    if a.csv:
        metrics_csv_a_archivo(a.csv)

    ip = ip_local()
    log_comment("=== camara_pc | modo: %s ===", modo.mode_name())
    log_comment("target_fps=%d  intervalo=%d ms", config.TARGET_FPS,
                config.FRAME_INTERVAL_MS)
    log_comment("TLS: deshabilitado a proposito (captura en texto plano)")
    log_comment("ip local: %s", ip)

    cam = camera_begin()
    if cam is None:
        return 1                      # camera_begin() ya explico el motivo
    camera_log_config(cam)

    modo.mode_setup(cam, ip)

    metrics_begin()                   # imprime el encabezado CSV: de aca, datos

    t_fin = millis() + int(a.duracion * 1000) if a.duracion else None
    try:
        while True:
            modo.mode_loop()
            metrics_tick()
            # Cede CPU, como el delay(1) del firmware. Sin esto el loop gira al
            # 100% de un nucleo sin ganar nada: el ritmo lo pone el FrameClock.
            time.sleep(0.001)
            if t_fin is not None and millis() >= t_fin:
                log_comment("fin por --duracion")
                break
    except KeyboardInterrupt:
        log_comment("cortado por el usuario")
    finally:
        modo.mode_close()
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
