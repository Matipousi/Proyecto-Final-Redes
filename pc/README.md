# camara_redes — port a PC

Misma maqueta de medición que el firmware del XIAO ESP32S3, corriendo en una
PC con webcam. Sirve para que **todo el equipo trabaje con una sola placa**:
cada integrante corre esto en su máquina, captura su propio tráfico y las
capturas siguen siendo comparables entre sí y con las del ESP32.

Los tres transportes son los mismos, con los mismos puertos, los mismos bytes
en el cable y la misma instrumentación:

| Modo | Rol del equipo | Puertos | Quién abre la conexión |
|---|---|---|---|
| 1 `HTTP MJPEG` | **servidor** | escucha en 80 (UI) + **81** (stream) | el cliente → la PC |
| 2 `WebSocket` | **servidor** | escucha en 80 (UI + WS `/stream`) | el cliente → la PC |
| 3 `MQTT` | **cliente** | **no escucha ninguno** | la PC → el broker :1883 |

Cada archivo es espejo de su par en el firmware, para que se puedan leer en
paralelo:

```
config.py            <->  config.h
metrics.py           <->  metrics.h / metrics.cpp
frame_clock.py       <->  frame_clock.h
camera.py            <->  camera.h / camera.cpp   (webcam en vez de OV2640)
pages.py             <->  los R"HTML(...)" de los dos modos servidor
mode_http_mjpeg.py   <->  mode_http_mjpeg.cpp
mode_websocket.py    <->  mode_websocket.cpp
mode_mqtt.py         <->  mode_mqtt.cpp
camara_pc.py         <->  camara_redes.ino
(no hay net.py)      <->  net.h / net.cpp         (el WiFi lo maneja el SO)
```

---

## 1. Instalación

Hace falta **Python 3.9 o más nuevo**.

### Windows

```powershell
py -m pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m pip install -r requirements.txt
```

La única dependencia es `opencv-python`, y **solo para usar la webcam real**.
Con `--fuente sintetica` el programa corre con la biblioteca estándar pelada,
sin instalar nada: útil para una máquina sin cámara, o para probar el transporte
con la webcam ocupada.

---

## 2. Correr cada modo

```bash
python3 camara_pc.py --modo 1          # HTTP MJPEG : abrir http://<ip>/
python3 camara_pc.py --modo 2          # WebSocket  : abrir http://<ip>/
python3 camara_pc.py --modo 3 --broker 192.168.1.100
```

El modo también se puede fijar en `config.py` (`STREAM_MODE`), igual que en el
firmware. `--modo` lo sobreescribe sin tocar el archivo, como hace
`-DSTREAM_MODE=n` en PlatformIO.

Opciones que más se usan:

| Opción | Para qué |
|---|---|
| `--fuente sintetica` | sin webcam |
| `--fps 10` | FPS objetivo |
| `--framesize VGA` | `QVGA`, `VGA`, `SVGA`, `HD`, `UXGA` |
| `--calidad 70` | calidad JPEG de OpenCV (**escala inversa** a la del firmware) |
| `--camara 1` | si hay varias webcams |
| `--duracion 60` | corta solo tras N segundos: corridas repetibles |
| `--bind 127.0.0.1` | escuchar solo local (ver §3) |
| `--calibrar` | igualar el tamaño de frame con el del ESP32 |

Grabar el CSV de una corrida:

```bash
python3 camara_pc.py --modo 1 --duracion 60 > modo1_pc.csv
```

El CSV va a **stdout** y los comentarios `#` a **stderr**, así que el archivo
queda limpio y los avisos se siguen viendo en la terminal. Es el mismo formato
que emite el firmware por el puerto serie, de modo que el mismo script de
pandas lee los dos:

```python
df = pd.read_csv("modo1_pc.csv", comment="#")
```

---

## 3. La trampa del loopback — leer antes de medir

**Si el emisor y el receptor son la misma máquina, el tráfico sale por loopback
y la segmentación TCP no se parece en nada a la del ESP32 por WiFi.** La MTU de
`lo0` es de 16 KB o más, así que un frame de 20 KB viaja en 1 o 2 paquetes en
vez de los ~15 segmentos de 1460 bytes que se ven en una red real. El conteo de
paquetes, el tamaño de los segmentos y los tiempos quedan todos distorsionados.

Lo que **sí** sigue siendo válido en loopback: el diálogo de protocolo (los
handshakes, el `101`, el `CONNECT`/`CONNACK`), el conteo de frames, los
boundaries del multipart, los opcodes WebSocket y los tópicos MQTT.

Lo que **no**: tamaño de segmento, cantidad de paquetes, retransmisiones,
ventana TCP, tiempos entre paquetes.

### Cómo evitarla

**Lo correcto: dos máquinas en la misma LAN.** Una corre `camara_pc.py` y la
otra el navegador o el verificador. Ahí el MSS es ~1460 y la captura es
comparable con la del ESP32.

```bash
# en la máquina A (la "cámara")
python3 camara_pc.py --modo 1

# en la máquina B (la que captura, con Wireshark abierto)
python3 ../tools/verificar_mjpeg.py <ip-de-A> --puerto 81
```

Para el modo 3, el broker tiene que estar en la máquina que captura, igual que
con el ESP32 (la red es conmutada).

**Si hay una sola máquina**, en macOS se puede bajar la MTU del loopback para
que se parezca a Ethernet:

```bash
sudo ifconfig lo0 mtu 1500        # volver atrás: sudo ifconfig lo0 mtu 16384
```

En Windows la MTU del loopback no se puede cambiar, y además capturar loopback
necesita el adaptador de Npcap. Con una sola máquina Windows, conviene usar la
captura para analizar **protocolo** y no segmentación, y decirlo en el informe.

---

## 4. Puertos privilegiados

En **Linux** los puertos menores a 1024 necesitan root. En **Windows no**. En
**macOS (10.14 o más nuevo)** un usuario normal ata el 80 y el 81 **solo si
escucha en todas las interfaces** (`BIND_HOST = "0.0.0.0"`, el default); con
`--bind 127.0.0.1` vuelve a pedir root (verificado en macOS). Si no se puede atar el puerto, el programa avisa y cae a
**8080 / 8081**:

```
# aviso: no pude atar el puerto 80 para la UI, uso el 8080
# aviso: OJO, cambia los filtros de Wireshark.
```

Eso **cambia los filtros de Wireshark**, así que para la corrida de medición
conviene usar los puertos reales:

```bash
sudo python3 camara_pc.py --modo 1        # solo en Linux
```

El fallback también salta si el puerto está ocupado (otro servidor web, o una
corrida anterior que quedó abierta).

Para desactivar el fallback y que falle fuerte en lugar de cambiar de puerto en
silencio: `PORT_FALLBACK_ENABLED = False` en `config.py`.

---

## 5. Igualar los bytes con el ESP32

La OV2640 comprime en el sensor con una escala de calidad de **10 a 63** donde
**menor es mejor**. OpenCV usa **0 a 100** donde **mayor es mejor**, y encima es
otro codificador. **Los números no se corresponden.** Lo que hay que igualar
para que los bitrates sean comparables es el **tamaño medio del frame**:

```bash
python3 camara_pc.py --calibrar
```

Mide el JPEG a varias calidades y recomienda la que cae más cerca de la franja
medida en el firmware (VGA q12 → 17-23 KB por frame, ~20 KB de media):

```
 calidad     media       min       max    kbit/s
------------------------------------------------
      60     15874     14201     17330      1270
      65     18992     17044     20611      1519
      70     22410     20118     24402      1793
...
mas cercano al ESP32: calidad 68
```

Después, poner ese valor en `CAM_JPEG_QUALITY_CV` y usar el mismo en todas las
máquinas del equipo, o las capturas de cada uno no serán comparables entre sí.

---

## 6. Qué es idéntico y qué no

Esto es lo que hay que poder defender en el informe.

### Idéntico (verificado byte a byte)

| | Detalle |
|---|---|
| Puertos y dirección de la conexión | 80 / 81 escuchando; MQTT saliente al 1883 |
| Respuesta que abre el stream MJPEG | mismas cabeceras, mismo `boundary=frameboundary` |
| Formato de cada parte | `\r\n--boundary\r\n` adelante, `Content-Length`, `X-Slot`, `X-Frame-N`, `X-Timestamp-Ms`, y **2 escrituras por frame** |
| Handshake WebSocket | `101 Switching Protocols` + `Sec-WebSocket-Accept` |
| Frames WebSocket | opcode `0x2`, `FIN=1`, sin máscara, un JPEG por mensaje |
| Diálogo MQTT | `CONNECT` (protocolo 4, clean session, keepalive 15) → `CONNACK`, `SUBSCRIBE` → `SUBACK`, `PUBLISH` QoS 0, `PINGREQ`/`PINGRESP` cada ~15 s |
| Tópicos y comandos MQTT | `camara/telemetria`, `camara/imagen`, `camara/cmd` con `snapshot`, `stream:on`, `stream:off` |
| Columnas del CSV | `ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped` |
| Esquema del JSON de telemetría | mismos campos, mismo orden, mismo `drop_detail` |
| Semántica de `dropped` | ranura pedida por el FPS objetivo que no salió |
| Atribución de causa | una escritura más larga que una ranura va a `bp`, no a `sched` |
| Política de un solo espectador | el segundo cliente recibe `503` |
| Páginas de control | copiadas literales del firmware (`pages.py`) |
| Tope de payload MQTT | 65000 B, respetado aunque en Python no haga falta, para que los descartes coincidan |

### Diferente, y por qué

| | Firmware | PC |
|---|---|---|
| `heap_free` | `ESP.getFreeHeap()` | **siempre `-1`**: no hay análogo. La columna se mantiene para que el mismo pandas lea los dos CSV |
| `rssi` | `WiFi.RSSI()` | `-1` salvo que se active `RSSI_ENABLED`. En Windows `netsh` da **porcentaje de calidad**, no dBm: se convierte con la fórmula estándar y sirve para ver tendencia, no como medición absoluta |
| `capture_ms` | captura (la OV2640 ya entrega JPEG) | captura **+ compresión en CPU**: es el análogo honesto, porque en los dos casos mide "cuánto tardé en tener el JPEG listo" |
| `send_ms` del modo 2 | la librería es asincrónica, así que la fila del CSV se **difiere** hasta que la cola drena | el socket es bloqueante: el `sendall()` ya es el tiempo hasta soltar el frame. Mismo significado, camino distinto |
| Contrapresión del modo 2 | cola de la librería llena (`queueLen() > 0`) | socket no escribible (`select`). Mismo efecto: como mucho **un frame en vuelo**, el nuevo se descarta y se cuenta |
| Reconexión de red | `net.cpp` reconecta el WiFi con backoff | no existe: el SO maneja la asociación. El backoff del MQTT sí está |
| `client_id` de MQTT | `xiao-cam` | `pc-cam-<hostname>`: si dos integrantes usan el mismo id contra el mismo broker, MQTT 3.1.1 obliga al broker a patear al primero |
| Calidad JPEG | 10-63, menor es mejor | 0-100, mayor es mejor, otro codificador. Se iguala por bytes (§5) |
| Trampas de hardware | PSRAM, `fb_count`, `esp_camera_fb_return()`, USB CDC bloqueante | no aplican |

---

## 7. Qué esperar en Wireshark

La tabla del [README principal](../README.md) §5 vale **tal cual**, con dos
salvedades:

1. Si no se corrió con `sudo`, los puertos son 8080 y 8081 en vez de 80 y 81, y
   hay que ajustar los filtros.
2. El `Decode As… → HTTP` del puerto 81 sigue siendo necesario (Wireshark no
   decodifica ni el 81 ni el 8081 como HTTP por defecto).

Y recordar la §3 de este archivo: en loopback la segmentación no es comparable.

---

## 8. Verificación

Los tres verificadores de `tools/` sirven **sin cambios** contra este port,
porque los bytes son los mismos:

```bash
python3 ../tools/verificar_mjpeg.py 127.0.0.1 --puerto 8081    # modo 1
python3 ../tools/verificar_ws.py    127.0.0.1 --puerto 8080    # modo 2
python3 ../tools/verificar_mqtt.py  127.0.0.1 --snapshot       # modo 3
```

### Estado medido

Todo lo de abajo se corrió de verdad, con `--fuente sintetica` sobre loopback,
en macOS. **La webcam real no se pudo probar en este entorno** (sin permiso de
cámara y sin OpenCV instalado): el camino `WebcamSource` está escrito contra la
API de OpenCV pero **no ejecutado**. Es lo primero que conviene probar.

| Modo | Verificador | Resultado |
|---|---|---|
| 1 HTTP MJPEG | `verificar_mjpeg.py` | **OK** — 10.12 fps, 1620 kbit/s, JPEG 18-21 KB, 0 descartes, `X-Slot` continuo, deriva de latencia 0 ms |
| 2 WebSocket | `verificar_ws.py` | **OK** — `101` y `Sec-WebSocket-Accept` correctos, 81 mensajes binarios, 0 fragmentados, 0 ping/pong, 0 enmascarados |
| 3 MQTT | `verificar_mqtt.py` | **OK** contra Mosquitto 2.1.2 real — `CONNECT (p4, c1, k15)`, `CONNACK`, `SUBSCRIBE`/`SUBACK`, telemetría 1.00/s, `snapshot` devuelve JPEG íntegro, QoS 0, `PINGREQ`/`PINGRESP` a los ~15 s |

Cruces que dieron bien:

- CSV `dropped` = descartes contados en la red = 0.
- Encabezado del CSV idéntico al del firmware, columna por columna.
- `slot() == frames_enviados + descartes` bajo jitter aleatorio.
- **Atribución de causa**: con un cliente que leía a 3.9 KiB/s contra ~200 KiB/s,
  el resultado fue `bp: 18, sched: 0` — misma forma que la reproducción
  verificada en el firmware (`bp: 169, sched: 0`).

### Comparación con el ESP32 (modo 1, VGA, 10 fps objetivo)

| | ESP32 por WiFi | PC por loopback |
|---|---|---|
| fps | 10.04 | 10.12 |
| bitrate | 1820 kbit/s | 1620 kbit/s |
| JPEG | 17-23 KB | 18-21 KB |
| `send_ms` mediana | **28 ms** | **0-1 ms** |

Los tres primeros renglones son comparables. El `send_ms` **no**: es justamente
el efecto del loopback (§3). Medido entre dos máquinas de la LAN tiene que
acercarse al número del ESP32.

---

## 9. Levantar el Mosquitto de prueba

Igual que para el firmware, con la config que ya está en el repo:

```bash
mosquitto -c ../tools/mosquitto.conf -v
```

Corrige los dos defaults de Mosquitto 2.x que rompen esto: que solo escuche en
`127.0.0.1` y `allow_anonymous false`. Después, apuntar el programa ahí:

```bash
python3 camara_pc.py --modo 3 --broker <ip-del-broker>
```

```bash
mosquitto_sub -h <broker> -t 'camara/#' -v                  # ver todo
mosquitto_pub -h <broker> -t 'camara/cmd' -m 'snapshot'     # pedir una foto
mosquitto_pub -h <broker> -t 'camara/cmd' -m 'stream:on'    # video continuo
```

---

## 10. Sin TLS y sin autenticación, a propósito

Igual que en el firmware: la captura tiene que poder leerse en claro, o el
trabajo pierde sentido. Los ganchos están comentados al final de `config.py`.

**Esto no se pone en una red que no sea la del laboratorio.** El modo 1 y el 2
abren puertos en la máquina sin ninguna autenticación: cualquiera en la misma
red ve la webcam.

---

## 11. Si se toca el HTML del firmware

`pages.py` tiene las páginas copiadas literales. Para re-extraerlas:

```bash
python3 regenerar_pages.py
```
