"""
camera.py  -  Espejo de camera.h / camera.cpp.

En el firmware esto inicializa la OV2640 sobre la placa Sense. Aca entrega lo
mismo (bytes JPEG listos para mandar) desde la webcam de la PC.

Dos fuentes:
  "webcam"     camara real via OpenCV. Es la que se usa para medir.
  "sintetica"  patron generado. Sirve si la maquina no tiene webcam o la tiene
               ocupada, y para probar el transporte sin depender de la camara.
               Funciona SIN NINGUNA dependencia.

Diferencias con el firmware que importan para la comparacion:

  * La OV2640 comprime a JPEG EN EL SENSOR; la webcam entrega cuadros sin
    comprimir y el JPEG lo hace la CPU. Por eso 'capture_ms' en la PC incluye
    captura + compresion: es el analogo honesto de lo que en el ESP32 mide
    esp_camera_fb_get(), que tambien devuelve el JPEG ya armado.

  * La escala de calidad es INVERSA y no son el mismo codificador, asi que los
    numeros no se corresponden. Lo que hay que igualar es el TAMANIO MEDIO del
    frame: para eso esta 'python3 camara_pc.py --calibrar'.
"""
import base64
import struct
import time

import config
from metrics import log_comment, millis

try:
    import cv2
    _HAY_CV2 = True
except ImportError:
    _HAY_CV2 = False


# -----------------------------------------------------------------------------
# Fuente 1: webcam real (OpenCV)
# -----------------------------------------------------------------------------
class WebcamSource:
    def __init__(self, indice, framesize_name, calidad):
        self.framesize_name = framesize_name
        self.calidad = calidad
        self._cap = None
        self._indice = indice

    def open(self):
        # CAP_DSHOW en Windows: con el backend por defecto (MSMF) abrir la
        # camara puede tardar varios segundos y algunos drivers ignoran el
        # pedido de resolucion.
        if config.CAM_SOURCE == "webcam" and hasattr(cv2, "CAP_DSHOW") and \
           __import__("sys").platform.startswith("win"):
            self._cap = cv2.VideoCapture(self._indice, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(self._indice)

        if not self._cap.isOpened():
            log_comment("ERROR: no se pudo abrir la camara %d", self._indice)
            if __import__("sys").platform == "darwin":
                # macOS le da el permiso a la APP que lanzo el proceso (Terminal,
                # iTerm, VS Code), no a Python. Sin permiso, isOpened() da False
                # y parece que no hay camara.
                log_comment("ERROR: en macOS casi siempre es el permiso de camara:")
                log_comment("ERROR: Ajustes -> Privacidad y seguridad -> Camara -> "
                            "habilitar la terminal o VS Code, y reiniciarla")
            log_comment("ERROR: probar otro CAM_INDEX, o CAM_SOURCE = \"sintetica\"")
            return False

        w, h = config.FRAMESIZE[self.framesize_name]
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        # Buffer de 1: queremos el cuadro MAS NUEVO, no una cola. Es el
        # equivalente de CAMERA_GRAB_LATEST del firmware; con cola, el video
        # llegaria atrasado respecto de los timestamps del CSV.
        try:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        real_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        real_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (real_w, real_h) != (w, h):
            # No es fatal: se reescala al tamanio pedido para que el payload sea
            # comparable con el del ESP32. Pero conviene saberlo.
            log_comment("aviso: la camara entrega %dx%d, se reescala a %dx%d",
                        real_w, real_h, w, h)

        # Cuadro de descarte: las webcams entregan los primeros cuadros oscuros
        # o en blanco mientras ajustan exposicion, y no queremos ese frame
        # contaminando la primera fila del CSV.
        self._cap.read()
        return True

    def set_framesize(self, name):
        if name not in config.FRAMESIZE:
            return False
        w, h = config.FRAMESIZE[name]
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.framesize_name = name
        return True

    def capture(self):
        """Devuelve (jpeg_bytes | None, capture_ms)."""
        t0 = millis()
        ok, cuadro = self._cap.read()
        if not ok or cuadro is None:
            return None, millis() - t0

        w, h = config.FRAMESIZE[self.framesize_name]
        if cuadro.shape[1] != w or cuadro.shape[0] != h:
            cuadro = cv2.resize(cuadro, (w, h), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", cuadro,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.calidad])
        if not ok:
            return None, millis() - t0
        return buf.tobytes(), millis() - t0

    def close(self):
        if self._cap is not None:
            self._cap.release()


# -----------------------------------------------------------------------------
# Fuente 2: sintetica
# -----------------------------------------------------------------------------
# JPEG valido de 16x16, reducido a los segmentos imprescindibles (DQT, SOF0,
# DHT, SOS). Se usa como semilla cuando no hay OpenCV: se la rellena con un
# segmento COM (0xFFFE) hasta el tamanio pedido. El resultado sigue siendo un
# JPEG decodificable (SOI/EOI correctos, Content-Length exacto), que es lo que
# necesita el transporte y lo que validan los verificadores.
_SEMILLA_JPEG = base64.b64decode(
    "/9j/wAALCAAQABABAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9sAQwAPDw8PDw8aDw8aJBoaGiQxJCQkJDE+MTExMTE+Sz4+Pj4+PktLS0tLS0tLWlpaWlpaaWlpaWl2dnZ2dnZ2dnZ2/9oACAEBAAA/AOTtYeldFaw9K//QyLWHpXRWsPSv/9k="
)


def _jpeg_rellenado(objetivo_bytes, variacion):
    """JPEG valido del tamanio pedido, rellenando con segmentos COM."""
    base = _SEMILLA_JPEG
    faltan = max(0, objetivo_bytes - len(base))
    relleno = b""
    while faltan > 0:
        # Un COM lleva su longitud en 2 bytes incluyendo el campo: 65533 de tope.
        n = min(faltan, 65533)
        if n < 3:
            break
        cuerpo = bytes((i * 31 + variacion) % 251 for i in range(n - 2))
        relleno += b"\xff\xfe" + struct.pack(">H", n) + cuerpo
        faltan -= n
    # El COM va despues del SOI, antes del resto.
    return base[:2] + relleno + base[2:]


class SyntheticSource:
    """Sin OpenCV: JPEG valido del tamanio tipico del ESP32, con variacion.
    Con OpenCV: patron animado real, con entropia parecida a la de una camara."""

    def __init__(self, framesize_name, calidad):
        self.framesize_name = framesize_name
        self.calidad = calidad
        self._n = 0

    def open(self):
        if not _HAY_CV2:
            log_comment("fuente sintetica SIN OpenCV: el payload es un JPEG valido")
            log_comment("relleno al tamanio tipico, pero la imagen no es video")
            return True

        # El patron generado no comprime como una camara: a la calidad de
        # config.py da ~3 veces los bytes del ESP32 (61 KB contra 20 KB en VGA),
        # y una prueba de transporte con el triple de carga no es comparable.
        # Se elige UNA VEZ la calidad que cae mas cerca del tamanio tipico del
        # firmware y queda fija toda la corrida (no se adapta frame a frame: eso
        # esconderia variaciones de bitrate).
        objetivo = self._objetivo_bytes()
        mejor, mejor_dif = self.calidad, None
        for q in range(95, 9, -5):
            self.calidad = q
            n = len(self.capture()[0])
            if mejor_dif is None or abs(n - objetivo) < mejor_dif:
                mejor, mejor_dif = q, abs(n - objetivo)
        self.calidad = mejor
        self._n = 0
        log_comment("fuente sintetica: calidad %d elegida para ~%d B por frame "
                    "(tamanio tipico del ESP32)", mejor, objetivo)

        # Captura de descarte, como el primer frame del firmware y el de la
        # webcam: la primera llamada a OpenCV inicializa cosas y puede tardar.
        # Sin esto, ese frame se come varias ranuras y aparecen como 'sched'.
        self.capture()
        self._n = 0
        return True

    def _objetivo_bytes(self):
        w, h = config.FRAMESIZE[self.framesize_name]
        return int(20000 * (w * h) / (640 * 480))

    def set_framesize(self, name):
        if name not in config.FRAMESIZE:
            return False
        self.framesize_name = name
        return True

    def capture(self):
        t0 = millis()
        self._n += 1
        w, h = config.FRAMESIZE[self.framesize_name]

        if _HAY_CV2:
            import numpy as np
            # Patron movil con ruido: sin ruido el JPEG comprime tanto que el
            # bitrate no se parece en nada al de una camara real.
            yy, xx = np.mgrid[0:h, 0:w]
            fase = self._n * 6
            img = np.zeros((h, w, 3), dtype=np.uint8)
            img[:, :, 0] = ((xx * 2 + fase) % 256).astype(np.uint8)
            img[:, :, 1] = ((yy * 2 - fase) % 256).astype(np.uint8)
            img[:, :, 2] = (((xx + yy) + fase * 2) % 256).astype(np.uint8)
            img = cv2.add(img, np.random.randint(0, 48, img.shape, dtype=np.uint8))
            cv2.putText(img, f"SINTETICO {self._n}", (12, h - 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            ok, buf = cv2.imencode(".jpg", img,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), self.calidad])
            if not ok:
                return None, millis() - t0
            return buf.tobytes(), millis() - t0

        # Sin OpenCV: tamanio tipico del ESP32 en VGA q12 (17-23 KB), escalado
        # por area para los otros framesize, con +-8% de variacion por frame.
        objetivo = self._objetivo_bytes()
        objetivo = int(objetivo * (0.92 + 0.16 * ((self._n * 37 % 100) / 100.0)))
        return _jpeg_rellenado(objetivo, self._n), millis() - t0

    def close(self):
        pass


# -----------------------------------------------------------------------------
# API, con los mismos nombres que en el firmware
# -----------------------------------------------------------------------------
def camera_begin():
    """Devuelve la fuente lista, o None. En el firmware esto se detiene si no
    encuentra PSRAM; aca el fallo equivalente es no poder abrir la camara."""
    if config.CAM_SOURCE == "webcam":
        if not _HAY_CV2:
            log_comment("ERROR: falta OpenCV y CAM_SOURCE es \"webcam\"")
            log_comment("ERROR: instalar con:  pip install opencv-python")
            log_comment("ERROR: o usar CAM_SOURCE = \"sintetica\" / --fuente sintetica")
            return None
        src = WebcamSource(config.CAM_INDEX, config.CAM_FRAMESIZE,
                           config.CAM_JPEG_QUALITY_CV)
    else:
        src = SyntheticSource(config.CAM_FRAMESIZE, config.CAM_JPEG_QUALITY_CV)

    return src if src.open() else None


def camera_log_config(src):
    w, h = config.FRAMESIZE[src.framesize_name]
    log_comment("camara: %s %dx%d, calidad JPEG OpenCV %d, fuente %s",
                src.framesize_name, w, h, src.calidad, config.CAM_SOURCE)
    if not _HAY_CV2:
        log_comment("OpenCV no instalado: sin webcam real")
