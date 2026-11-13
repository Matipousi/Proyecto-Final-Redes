# camara_redes — tres arquitecturas de transporte para el mismo stream

> **¿Vas a correrlo en tu computadora con Windows y tu webcam, sin la placa?**
> Seguí el tutorial paso a paso: **[README-WINDOWS.md](README-WINDOWS.md)**.

Maqueta de medición para **Redes de Datos**. Un solo firmware para el
**Seeed Studio XIAO ESP32S3 Sense** que transporta el mismo video de tres formas
distintas, con la **misma instrumentación en las tres**, para poder comparar las
capturas de Wireshark entre sí.

El firmware es el instrumento, no el entregable. Todas las decisiones de diseño
se tomaron a favor de la **observabilidad**: si algo se podía hacer más rápido
pero menos visible en la captura, se hizo más visible.

| Modo | `STREAM_MODE` | Rol del equipo | Puertos | Quién abre la conexión |
|---|---|---|---|---|
| 1 | `MODE_HTTP_MJPEG` | **servidor** | escucha en 80 y 81 | la PC → el ESP32 |
| 2 | `MODE_WEBSOCKET` | **servidor** | escucha en 80 | la PC → el ESP32 |
| 3 | `MODE_MQTT` | **cliente** | **no escucha ninguno** | el ESP32 → el broker |

> **Hay una sola placa, pero no hace falta una por persona.** En
> [`pc/`](pc/README.md) está el mismo instrumento portado a Python: corre en
> cualquier Windows o macOS con webcam, habla **los mismos tres protocolos con
> los mismos bytes** y emite el mismo CSV. Así cada integrante del equipo
> captura su propio tráfico desde su casa. Leer la *trampa del loopback* de ese
> README antes de medir.

---

## 1. Hardware y entorno

- Seeed Studio XIAO ESP32S3 **Sense** (ESP32-S3R8, 8 MB PSRAM OPI, 8 MB flash)
- Cámara OV2640 sobre la placa de expansión Sense
- WiFi 802.11 b/g/n, **solo 2.4 GHz**, 1x1 HT20 → techo real ~20-30 Mbps
- arduino-esp32 core **3.x** (probado con 3.3.11)

Con `FRAMESIZE_VGA`, calidad 12 y 10 fps el stream ronda **2-4 Mbps**, bien por
debajo del techo del enlace: los descartes que se midan van a ser del equipo o
del transporte, no del canal saturado. Eso es a propósito.

---

## 2. Entorno A — Arduino IDE

Placa: **XIAO_ESP32S3** (paquete `esp32` de Espressif).

| Herramientas → | Valor | Por qué |
|---|---|---|
| **PSRAM** | **`OPI PSRAM`** | **Viene en `Disabled` por defecto.** Sin esto no hay dónde poner los frame buffers y el firmware se detiene con un mensaje explícito. |
| USB CDC On Boot | `Enabled` | Para ver el CSV por el Monitor Serie desde el arranque |
| Flash Size | `8MB (64Mb)` | |
| Partition Scheme | `Default with spiffs (3MB APP)` | Alcanza y sobra: el sketch más grande usa ~1 MB |
| CPU Frequency | `240MHz (WiFi)` | |
| Upload Speed | `921600` | |
| Core Debug Level | `None` | Los logs del core ensuciarían el CSV |

Monitor Serie a **115200** (`SERIAL_BAUD` en `config.h`).

> Si el core no aparece: Preferencias → *Gestor de URLs Adicionales de Tarjetas* →
> `https://espressif.github.io/arduino-esp32/package_esp32_index.json`, después
> Gestor de tarjetas → `esp32`.

### Librerías

| Modo | Librería | Autor | Instalar desde |
|---|---|---|---|
| 1 | *(ninguna, solo el core)* | | |
| 2 | **ESP Async WebServer** | ESP32Async | Gestor de Librerías |
| 2 | **Async TCP** | ESP32Async | Gestor de Librerías (**no viene como dependencia**, hay que instalarla aparte) |
| 3 | **PubSubClient** | Nick O'Leary | Gestor de Librerías |

Ojo con los forks: para core 3.x hay que usar los de **ESP32Async**, no los
originales de `me-no-dev`, que ya no compilan contra el core nuevo.

Como cada `mode_*.cpp` está entero dentro de un `#if STREAM_MODE == ...`, los
archivos de los modos que no se compilan quedan **vacíos**: para compilar el
modo 1 no hace falta tener instaladas las librerías de los modos 2 y 3.

---

## 3. Entorno B — PlatformIO en VS Code

`platformio.ini` ya está en el repo con **un environment por modo**. Acá **no
hace falta editar `config.h` para cambiar de modo**: cada env fuerza el suyo con
`-D STREAM_MODE=n` (por eso el `#define` tiene un `#ifndef` alrededor). Lo que sí
hay que editar son el SSID, la contraseña y la IP del broker.

| Environment | Modo |
|---|---|
| `modo1_http_mjpeg` | 1 — HTTP MJPEG (por defecto) |
| `modo2_websocket` | 2 — WebSocket |
| `modo3_mqtt` | 3 — MQTT |

### Por qué no la plataforma oficial

```ini
platform = https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip
```

La plataforma oficial `platformio/espressif32` (7.1.0) todavía trae
**arduino-esp32 2.0.17**. Este proyecto necesita **3.x**: usa
`WiFiServer::accept()`, `NetworkClient` y `setConnectionTimeout()`, que en 2.x no
existen, y `ESPAsyncWebServer` de ESP32Async tampoco compila contra 2.x. La build
de **pioarduino** `55.03.311` trae el core **3.3.11** exacto.

### Diferencias con el Arduino IDE

| | Arduino IDE | PlatformIO |
|---|---|---|
| Elegir el modo | editar `STREAM_MODE` en `config.h` | elegir el environment |
| **PSRAM OPI** | **hay que habilitarla a mano** en Herramientas | **ya viene habilitada**: el board define `memory_type = qio_opi` y `-DBOARD_HAS_PSRAM` |
| USB CDC / modo USB | Herramientas | ya en el board (`ARDUINO_USB_MODE=1`, `ARDUINO_USB_CDC_ON_BOOT=1`) |
| Particiones | Herramientas | `board_build.partitions = default_8MB.csv` |
| Librerías | Gestor de Librerías | `lib_deps`, se bajan solas |

Las dos rutas conviven: `src_dir = .` deja el layout de sketch de Arduino, así
que **la misma carpeta se abre en los dos entornos** y produce el mismo binario.

### Comandos

```bash
# compilar un modo
pio run -e modo1_http_mjpeg

# compilar los tres (chequeo rápido después de tocar algo)
# OJO: "pio run" a secas compila SOLO el env de default_envs (el modo 1);
# hay que nombrarlos para que compile los tres.
pio run -e modo1_http_mjpeg -e modo2_websocket -e modo3_mqtt

# compilar y flashear
# (antes hay que poner WIFI_SSID / WIFI_PASS en config.h: si quedan en
#  "CAMBIAME", el firmware arranca, inicializa la cámara y se queda
#  reintentando la conexión con backoff, sin llegar a servir nada)
pio run -e modo1_http_mjpeg -t upload

# monitor serie
pio device monitor -e modo1_http_mjpeg

# ver qué puerto detecta
pio device list
```

Desde la barra inferior de VS Code: el ✓ compila, la → flashea, el enchufe abre
el monitor. El selector de environment (`env:...`) es el que decide **el modo**.

Si `pio` no está en el `PATH`, está en `~/.platformio/penv/bin/pio`.

### Grabar el CSV desde PlatformIO

`monitor_filters = esp32_exception_decoder` está puesto para que un panic se vea
con número de línea, y no toca las filas del CSV. Pero para **grabar** una
corrida conviene un terminal crudo, sin filtros ni banner:

```bash
pio device monitor --raw --quiet -b 115200 | tee modo1.csv
```

### Si un build de PlatformIO se corta a la mitad

PlatformIO no compila el `.ino` directamente: antes de armar la lista de fuentes
lo convierte en `camara_redes.ino.cpp`, lo deja **al lado del `.ino`** (no dentro
de `.pio/`) y lo borra al terminar. Si un build se interrumpe, ese archivo puede
quedar colgado, y entonces **el Arduino IDE compila los dos** y falla con
`setup()` y `loop()` duplicados. Se arregla borrándolo:

```bash
rm -f camara_redes.ino.cpp
```

Ya está en `.gitignore`.

---

## 4. Cómo compilar cada modo (Arduino IDE)

### Desde el IDE

Editar **una sola línea** en `config.h`:

```c
#define STREAM_MODE MODE_HTTP_MJPEG   // o MODE_WEBSOCKET, o MODE_MQTT
```

Antes de la primera compilación hay que poner el SSID, la contraseña y la IP del
broker en `config.h`.

### Desde la línea de comandos (opcional)

```bash
./tools/compilar.sh        # compila los tres modos y avisa si alguno rompe
./tools/compilar.sh 1      # solo el modo 1
```

El script fuerza el modo con `-DSTREAM_MODE=n` sin tocar `config.h` (por eso el
`#ifndef` alrededor del `#define`). Requiere `brew install arduino-cli`.

---

## 5. Los tres modos

### Modo 1 — `MODE_HTTP_MJPEG` · el equipo es servidor

- **Puerto 80**: página de control mínima (`WebServer` del core).
- **Puerto 81**: el stream, sobre un `WiFiServer` crudo.

Los puertos están separados **a propósito**: en la captura se ve al navegador
abrir una conexión corta al 80 para bajar el HTML y otra al 81 que ya no cierra.
Con un solo puerto las dos cosas se confundirían.

El stream es un único cuerpo HTTP **que nunca termina**:

```
HTTP/1.1 200 OK
Content-Type: multipart/x-mixed-replace; boundary=frameboundary

--frameboundary
Content-Type: image/jpeg
Content-Length: 28431
X-Slot: 147            <- número de ranura del reloj de FPS
X-Frame-N: 145         <- número de frame enviado
X-Timestamp-Ms: 14732

<28431 bytes de JPEG>
--frameboundary
...
```

`X-Slot` no es estándar y está puesto para el trabajo: avanza **también en las
ranuras perdidas**, así que **un salto en `X-Slot` dentro de la captura es un
frame descartado**, sin necesidad de abrir el CSV. `X-Frame-N` cuenta solo los
enviados y por eso nunca deja hueco: sirve para detectar pérdidas de la propia
captura.

Un solo espectador por vez; al segundo se le contesta **503** (visible en la
captura). Con dos clientes el ancho de banda se reparte y las métricas dejarían
de significar lo que dicen.

Abrir: `http://<ip>/` — el `<img>` apunta solo a `http://<ip>:81/stream`.

### Modo 2 — `MODE_WEBSOCKET` · el equipo es servidor

Un solo `AsyncWebServer` en el **puerto 80** que sirve la página cliente **y**
hace el upgrade en `/stream`. Un JPEG completo viaja **por mensaje binario**.
La librería puede fragmentar ese mensaje en varios *frames* WebSocket según el
espacio disponible en el buffer TCP; el navegador los reensambla y entrega un
solo `Blob` por JPEG.

La página muestra las resoluciones como dimensiones reales (320×240, 640×480 y
800×600) y permite cambiar resolución y FPS (1, 2, 5, 10, 15, 20, 25 o 30)
mientras el video continúa. Los pedidos viajan navegador → ESP32 como mensajes
WebSocket de texto,
y la confirmación vuelve por el mismo canal. Esto demuestra en vivo la ventaja
bidireccional frente al stream MJPEG. Para las capturas comparativas hay que
volver a **640×480 / 10 FPS** y no tocar los controles durante la corrida.
La vista previa conserva siempre un área 4:3 de 800×600; 640×480 y 320×240 se
escalan en el navegador para ocupar el mismo espacio que 800×600.

En Wireshark, los comandos de texto del navegador llevan `MASK=1`, como exige
WebSocket para cliente → servidor. Las confirmaciones de texto y los JPEG del
ESP32 llevan `MASK=0`, porque servidor → cliente no se enmascara.

El auto-ping está desactivado: no hay ping/pong intercalado. Sin usar los
controles, el tráfico de datos es video binario; al usarlos aparecen además
mensajes de texto claramente distinguibles.

Abrir: `http://<ip>/`.

### Modo 3 — `MODE_MQTT` · el equipo es cliente

El ESP32 **no abre ningún puerto**. Sale hacia el broker Mosquitto de la LAN.

| Tópico | Dirección | Contenido |
|---|---|---|
| `camara/telemetria` | pub, 1 Hz | JSON: `fps`, `bytes_s`, `rssi`, `heap_free`, `dropped`, `frames`, `uptime_s`, `drop_detail` |
| `camara/cmd` | sub | `snapshot` · `stream:on` · `stream:off` |
| `camara/imagen` | pub | el JPEG crudo |

Por defecto solo publica telemetría y saca fotos bajo demanda. Para comparar los
tres modos con **la misma carga de video** hay que poner `MQTT_STREAM_ENABLED 1`
en `config.h`, o publicar `stream:on` en `camara/cmd` en caliente.

Sin TLS y sin usuario/contraseña, a propósito (ver §11).

---

## 6. Qué esperar ver en Wireshark

**Esta es la tabla para verificar que la captura salió bien.** Si algo no
coincide, la captura está mal y no sirve para comparar.

| | **Modo 1 — HTTP MJPEG** | **Modo 2 — WebSocket** | **Modo 3 — MQTT** |
|---|---|---|---|
| **Dirección del SYN** | PC → ESP32 | PC → ESP32 | **ESP32 → broker** |
| **Puerto destino del SYN** | 80 (página) y **81** (stream) | 80 | **1883** |
| **Puerto origen** | efímero en la PC | efímero en la PC | **efímero en el ESP32** |
| **Puertos en escucha en el ESP32** | 80, 81 | 80 | **ninguno** |
| **Apertura del canal** | `GET /stream HTTP/1.1` → `HTTP/1.1 200 OK` con `Content-Type: multipart/x-mixed-replace; boundary=frameboundary` | `GET /stream` con `Upgrade: websocket` + `Sec-WebSocket-Key` → **`HTTP/1.1 101 Switching Protocols`** + `Sec-WebSocket-Accept` | `CONNECT` → `CONNACK`, después `SUBSCRIBE` → `SUBACK` |
| **Columna Protocol de un frame de video** | `TCP` (segmentos del cuerpo) — ver nota A | `WebSocket` | `MQTT` |
| **Unidad de video en la captura** | una *parte* del multipart: `--frameboundary` + `Content-Length` + JPEG | un mensaje WS **binario**; puede ocupar un frame (`Opcode=2`, `FIN=1`) o varios (`Opcode=2`, continuaciones `Opcode=0`, `FIN=1` solo en el último). Servidor→cliente usa `MASK=0` | un `Publish Message`, `Topic = camara/imagen`, QoS 0 |
| **Conexiones TCP durante el stream** | **2** al abrir: página en 80 y stream **permanente** en 81 | Normalmente **2** al abrir: página y upgrade WS, ambas en 80; queda **1 permanente** para WebSocket | **1**, permanente, saliente |
| **¿Hay FIN durante el stream?** | no, hasta que se cierre el navegador | no | no |
| **Tráfico de control periódico** | ninguno | ninguno (auto-ping apagado) | **`PINGREQ` / `PINGRESP` cada 15 s** |
| **¿Hay ACK de aplicación por frame?** | no (lo delimita `Content-Length`) | no | **no**: QoS 0 no lleva `PUBACK` |
| **Overhead por frame** | ~110 B de cabeceras de parte, en texto | 2-10 B de cabecera WS, binaria | ~2-5 B de cabecera fija + longitud del tópico |
| **Filtro de visualización** | `tcp.port == 81` | `websocket` | `mqtt` |
| **Cómo contar frames** | `http` + Follow TCP Stream, contar `--frameboundary` | `websocket.opcode == 2` | `mqtt.msgtype == 3 && mqtt.topic == "camara/imagen"` |
| **Ritmo esperado** | `TARGET_FPS` partes por segundo | `TARGET_FPS` mensajes por segundo | 1 `PUBLISH` por segundo en `camara/telemetria`; los de imagen solo con `stream:on` |

### Notas para que la captura salga bien

**A. El puerto 81 no se decodifica como HTTP solo.** El disector HTTP de
Wireshark cubre 80, 8080, 8088… pero **no el 81**. Sin esto solo se ven
segmentos TCP crudos. Botón derecho sobre un paquete → **Decode As…** → campo
*TCP port* `81` → **HTTP**. Recién ahí aparecen las cabeceras y el JPEG.

**B. En el modo 2, empezar la captura ANTES de abrir la página.** Wireshark
reconoce un flujo WebSocket solo si vio el handshake `101`. Si se arranca la
captura con la conexión ya abierta, los frames se ven como TCP sin disecar y no
hay forma de recuperarlo.

**C. En el modo 3, correr Mosquitto en la misma máquina donde corre Wireshark.**
La red es conmutada: si el broker está en otra máquina, la PC de captura **no ve
el tráfico** ESP32↔broker. Con el broker local, se captura en la interfaz de red
normal y se ve todo.

**D. En los modos 1 y 2, abrir el navegador en la máquina que captura**, por la
misma razón.

**E. Filtro de captura** (no de visualización) para dejar afuera todo lo demás,
poniendo la IP del ESP32:

```
host 192.168.1.50
```

**F. Filtros de visualización útiles**

```
tcp.port == 81                                  # modo 1: solo el stream
http.response.code == 200                       # modo 1: la respuesta que abre el multipart
tcp.port == 81 && tcp.flags.syn == 1            # modo 1: verificar que hay UN solo SYN

http.response.code == 101                       # modo 2: el upgrade, tiene que aparecer 1 vez
websocket.opcode == 2                           # modo 2: comienzo de cada mensaje JPEG
websocket.opcode == 0                           # modo 2: continuaciones si el mensaje fue fragmentado
websocket.opcode == 1                           # modo 2: comandos/confirmaciones de texto

mqtt                                            # modo 3: todo el diálogo MQTT
mqtt.msgtype == 1 || mqtt.msgtype == 2          # modo 3: CONNECT / CONNACK
mqtt.topic == "camara/telemetria"               # modo 3: solo telemetría
mqtt.msgtype == 12 || mqtt.msgtype == 13        # modo 3: PINGREQ / PINGRESP del keepalive
ip.src == 192.168.1.50 && tcp.flags.syn == 1    # modo 3: el SYN sale DEL ESP32
```

**G. Comprobar que en el modo 3 el equipo no escucha nada**, desde la PC:

```bash
nmap -Pn -p 1-1024 192.168.1.50     # en los modos 1 y 2 aparecen 80 (y 81); en el 3, nada
```

**H. Estadísticas que sirven para el informe:** *Statistics → Conversations →
TCP* (una fila por conexión: se ve la asimetría de bytes), y
*Statistics → I/O Graph* con el filtro de cada modo, para superponer el bitrate
de los tres.

---

## 7. La instrumentación

Idéntica en los tres modos: es lo que los hace comparables.

### CSV por el puerto Serie

Una línea por frame **enviado**, con encabezado:

```
ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped
14523,145,28431,52,31,214536,-47,3
```

Todo lo que **no** es una fila de datos sale con `#` adelante, así que el volcado
es un CSV válido tal cual:

```python
import pandas as pd
df = pd.read_csv("captura_modo1.csv", comment="#")
```

Para grabarlo, cualquier terminal serie sirve; por ejemplo:

```bash
arduino-cli monitor -p /dev/cu.usbmodem101 -c baudrate=115200 | tee modo1.csv
```

| Columna | Significado |
|---|---|
| `ts_ms` | `millis()` al terminar de enviar |
| `frame_n` | frames enviados con éxito desde el arranque |
| `bytes` | tamaño del JPEG (sin cabeceras de transporte) |
| `capture_ms` | tiempo dentro de `esp_camera_fb_get()` |
| `send_ms` | tiempo hasta soltar el frame a la red — ver abajo |
| `heap_free` | heap interno libre (no PSRAM) |
| `rssi` | dBm |
| `dropped` | **acumulado** de frames que el FPS objetivo pedía y no salieron |

**`send_ms` mide lo mismo en los tres modos, pero no de la misma manera:**

- **Modo 1**: tiempo de la escritura bloqueante al socket TCP.
- **Modo 2**: `binary()` es asincrónico y vuelve enseguida, así que medir el
  encolado sería mentir. La fila del CSV se **difiere** hasta que la cola del
  WebSocket se vacía, y `send_ms` es el tiempo de encolado a drenado.
- **Modo 3**: tiempo de `beginPublish()` + `write()` + `endPublish()`, que
  escriben directo al socket.

En los tres casos es *"cuánto tardó en soltar el frame a la red"*, no un
`memcpy`. Eso los hace comparables.

### `dropped`: qué cuenta y qué no

Un frame cuenta como descartado cuando **el reloj de FPS pidió una ranura y no
salió nada**. El reloj corre solo cuando hay a quién enviarle (hay cliente
conectado, o el broker está arriba): el tiempo ocioso no infla el contador.

El desglose por causa va en la telemetría, en `drop_detail`:

| Causa | Cuándo |
|---|---|
| `sched` | la ranura pasó mientras el equipo estaba ocupado con la anterior |
| `cap` | `esp_camera_fb_get()` devolvió `NULL` |
| `bp` | contrapresión: la cola del WebSocket no drenó, o poco heap |
| `noclient` | no había a quién enviarle |
| `tx` | el envío falló (publish rechazado, escritura corta) |

### Telemetría JSON

Los mismos números, una vez por segundo. El modo 3 la publica en
`camara/telemetria`; los modos 1 y 2 la exponen en `GET /status`, con **el mismo
formato**, para poder cruzarlos.

En la página del modo 1 el sondeo de `/status` **arranca apagado**: prendido
agregaría un GET por segundo al puerto 80 y ensuciaría la captura de referencia.
Hay un botón para prenderlo cuando haga falta.

---

## 8. Mosquitto de prueba (modo 3)

```bash
brew install mosquitto
```

Levantarlo en primer plano con la config de la maqueta, **en la misma máquina
donde corre Wireshark** (ver nota C):

```bash
mosquitto -c tools/mosquitto.conf -v
```

`tools/mosquitto.conf` corrige los dos defaults de Mosquitto 2.x que rompen esto:
solo escucha en `127.0.0.1` (el ESP32 no lo alcanzaría) y `allow_anonymous false`
(rechazaría la conexión). Los dos están abiertos **a propósito y solo para esta
etapa**.

Averiguar la IP de la máquina y ponerla en `MQTT_BROKER_IP` de `config.h`:

```bash
ipconfig getifaddr en0        # WiFi en macOS
```

### Probar desde otra terminal

```bash
# ver la telemetría llegar
mosquitto_sub -h localhost -t 'camara/telemetria' -v

# ver todo lo que publica el equipo
mosquitto_sub -h localhost -t 'camara/#' -v

# pedir una foto
mosquitto_pub -h localhost -t 'camara/cmd' -m 'snapshot'

# pedir una foto y guardarla en disco
mosquitto_sub -h localhost -t 'camara/imagen' -C 1 -F %x | xxd -r -p > snapshot.jpg

# prender / apagar el stream continuo, para comparar contra los modos 1 y 2
mosquitto_pub -h localhost -t 'camara/cmd' -m 'stream:on'
mosquitto_pub -h localhost -t 'camara/cmd' -m 'stream:off'
```

Si el firewall de macOS pregunta, hay que **permitir** las conexiones entrantes a
`mosquitto`; si no, el ESP32 se queda reintentando con backoff (y se ve en el
log serie).

---

## 9. Verificar el modo 1 sin abrir un navegador

```bash
python3 tools/verificar_mjpeg.py 192.168.1.50
```

Se conecta al puerto 81, valida el multipart y mide lo mismo que el firmware
reporta, pero **del lado de la red**:

- que la respuesta sea `200` con `multipart/x-mixed-replace` y su `boundary`
- que cada parte traiga `Content-Type: image/jpeg` y `Content-Length`
- que cada JPEG empiece en `FFD8` y termine en `FFD9` (si esto falla, el
  `Content-Length` no coincide con el frame y las partes se están desalineando)
- fps observado, bitrate, tamaño mínimo / promedio / máximo
- **los huecos en `X-Slot`**, que tienen que dar lo mismo que la columna
  `dropped` del CSV en esa ventana

Si los dos números coinciden, el CSV y la captura están contando lo mismo y se
puede confiar en los dos.

Para probar la herramienta sin tener la placa conectada:

```bash
python3 tools/verificar_mjpeg.py --autotest
```

Levanta un servidor que emite exactamente los mismos bytes que el firmware,
descarta tres ranuras a propósito, y verifica que las detecte.

### Medición de referencia (modo 1, hardware real)

Corrida de 10 s sobre la placa: `FRAMESIZE_VGA`, calidad 12, `TARGET_FPS 10`,
RSSI −37 dBm. Sirve como línea de base para saber si una corrida nueva pinta
bien antes de armar la captura.

| | Verificador (red) | CSV (serie) |
|---|---|---|
| frames | 98 | 98 |
| fps | 9,78 | 9,73 |
| **descartes** | **3** (huecos en `X-Slot`) | **3** (columna `dropped`) |
| JPEG mín/prom/máx | 21458 / 21550 / 21673 B | idéntico |

Que las dos columnas den lo mismo es **el** chequeo que importa: el contador del
firmware y lo que realmente salió por la red cuentan lo mismo.

Otros números de esa corrida:

| Métrica | Valor | Lectura |
|---|---|---|
| bitrate | 1686 kbit/s (206 KiB/s) | muy por debajo del techo de ~20-30 Mbps: el canal no es el cuello de botella |
| `capture_ms` prom / máx | 0,2 / 11 ms | con `GRAB_LATEST` y `fb_count 2` el frame ya está listo cuando se lo pide |
| `send_ms` prom / máx | 49,7 / 290 ms | **acá está la causa de los descartes**: un envío de 290 ms se come 2-3 ranuras de 100 ms |
| `heap_free` mín | 223.700 B | estable en 98 frames, sin fuga |
| `drop_detail` | `sched: 3`, resto en 0 | los tres descartes son de ranura, ninguno de captura ni de transporte |

---

## 10. Si el FPS se cae: cómo saber de quién es la culpa

El firmware está instrumentado para responder esto **sin adivinar**. Hay tres
sospechosos y cada uno deja una huella distinta.

### Paso 1 — mirar `drop_detail` en `/status`

| Qué se ve | Qué significa | Qué hacer |
|---|---|---|
| `bp` alto | **El consumidor no drena.** TCP frena al emisor. Típico: pestaña del navegador en segundo plano (los navegadores estrangulan las pestañas no visibles), o varios espectadores | Dejar la pestaña visible, o medir con `tools/verificar_mjpeg.py` en vez del navegador |
| `sched` alto **y** `bp` en 0 | El loop no llega a las ranuras por algo que no es el envío | Paso 2 |
| `cap` alto | La cámara no entrega frames | Revisar PSRAM y `fb_count` |
| `tx` alto | Fallan los envíos/publicaciones | Modo 3: broker caído, o frame > `MQTT_MAX_PAYLOAD_BYTES` |

`bp` y `sched` se distinguen bien porque el firmware **atribuye** la causa: si una
escritura tarda más que una ranura, las ranuras perdidas van a `bp`, no a `sched`
(ver §12). Sin esa atribución todo caía en `sched` y el desglose no servía.

### Paso 2 — encender el perfilador del loop

En `config.h`:

```c
#define DEBUG_LOOP_TIMING 1
```

Emite una línea `#` por segundo con el tiempo **máximo** de cada fase:

```
# loop: vueltas/s=500 max_ui=1 max_acc=0 max_send=0 max_vuelta=2 | pend=0 len=0 stream=0 srv=1
```

| Campo | Sano | Si está alto |
|---|---|---|
| `vueltas/s` | ~500 en reposo | si baja mucho, algo bloquea |
| `max_ui` | 1-2 ms | el servidor del puerto 80 se está trabando |
| `max_acc` | 0-3 ms | el accept del :81 se traba (no debería: es no bloqueante) |
| `max_send` | < intervalo de ranura | **la escritura TCP es el cuello**: enlace o consumidor |

Acordarse de volverlo a **0** para las corridas de medición: agrega una línea por
segundo al CSV.

### Paso 3 — descartar primero el puerto serie (la trampa cara)

**Antes de sospechar de la red, cerrar el monitor serie y volver a medir. Y si ya
estaba cerrado, abrirlo y medir de nuevo.** Si el resultado cambia, el problema
es la instrumentación, no el transporte.

El USB CDC nativo del ESP32-S3 **bloquea** al escribir cuando nadie drena el
puerto: el core espera hasta `20 × 100 ms = 2 segundos` por `write()`. Con una
fila de CSV por frame, el propio instrumento estrangula el stream. Medido sobre
esta placa, mismo instante y mismo RSSI:

| | fps | bitrate |
|---|---|---|
| nadie leyendo el serie | **0,40** | 27 kbit/s |
| serie siendo drenado | **9,71** | 643 kbit/s |

Veinticuatro veces de diferencia, **solo por tener el monitor abierto**.

Está resuelto con `SERIAL_TX_TIMEOUT_MS 0` (`config.h`), que hace la escritura no
bloqueante: si el buffer está lleno, la fila se pierde y el stream sigue. Es lo
correcto para un instrumento — la instrumentación nunca debe frenar lo que mide —
pero conviene saberlo:

- **Para capturar el CSV completo**: tener el monitor abierto de verdad. Con el
  puerto drenado no se pierde ninguna fila.
- **Para medir el transporte**: da igual que el monitor esté abierto o cerrado.
  Esa independencia es justamente lo que hay que verificar antes de confiar en
  una corrida.

Este caso es un buen ejemplo para el informe: el instrumento perturbaba la
medición, y la perturbación se veía como si fuera un problema de red.

### Paso 4 — recién ahora, la red

Con el `SERIAL_TX_TIMEOUT_MS` arreglado, medido sin nadie leyendo el serie:

| Config | RSSI | fps | bitrate | descartes |
|---|---|---|---|---|
| `FRAMESIZE_VGA` q12 | −43 dBm | **10,04** | 1820 kbit/s | 0 |
| `FRAMESIZE_QVGA` q12 | −75 dBm | **10,05** | 668 kbit/s | 0 |

Nótese que QVGA alcanzó el objetivo **incluso a −75 dBm**. Un RSSI pobre por sí
solo no explica un FPS caído: hay que ver `max_send` en el perfilador y `bp` en
`drop_detail`. Si los dos están bajos, el cuello no es la red.

### Presupuesto de bitrate

`bytes_por_frame × TARGET_FPS × 8` tiene que entrar con holgura en lo que
realmente da el enlace:

| Config | Bytes/frame | A 10 fps |
|---|---|---|
| `FRAMESIZE_VGA` q12 | ~17-23 KB | 1,4-1,8 Mbps |
| `FRAMESIZE_QVGA` q12 | ~5-8 KB | 0,4-0,7 Mbps |

Si el enlace no da, bajar `CAM_FRAMESIZE` o subir `CAM_JPEG_QUALITY` (número más
alto = menos bytes). **Lo que no conviene es dejar un FPS objetivo que el enlace
no sostiene**: la corrida se llena de descartes y deja de ser comparable con las
otras dos.

---

## 11. Sin TLS y sin autenticación, a propósito

Toda la maqueta va **en texto plano**. No es un descuido: si se cifra, en la
captura no se ve nada más que TLS y el trabajo pierde sentido. Concretamente:

- MQTT en 1883, sin usuario ni contraseña
- HTTP y WebSocket sin `https://` / `wss://` y sin autenticación
- Mosquitto con `allow_anonymous true`

Los ganchos para la etapa siguiente están **comentados** al final de `config.h`
(`USE_TLS`, `MQTT_PORT_TLS`, `MQTT_CA_CERT`, `MQTT_USER`/`MQTT_PASS`,
`HTTP_AUTH_USER`/`HTTP_AUTH_PASS`) y en `tools/mosquitto.conf` (listener 8883).

**Esto no se pone en una red que no sea la del laboratorio.**

---

## 12. Trampas conocidas y dónde están resueltas

| Trampa | Dónde | Cómo |
|---|---|---|
| Pinout equivocado | `camera_pins.h` | Bloque `CAMERA_MODEL_XIAO_ESP32S3` copiado literal del core (vive en la carpeta de *ejemplos*, no está en el include path de un sketch). Cualquier otro define da `#error`. |
| `fb_location` / `grab_mode` / `fb_count` | `camera.cpp` | `CAMERA_FB_IN_PSRAM`, `CAMERA_GRAB_LATEST`, `fb_count = 2`. `GRAB_LATEST` evita la latencia creciente que haría que el video vaya atrasado respecto de los timestamps del CSV. |
| Olvidarse un `esp_camera_fb_return()` | `fb_guard.h` | RAII: el destructor devuelve el buffer al salir del scope. No depende de acordarse en cada `return` ni en los caminos de error. Es la causa del clásico "se cuelga después de N frames". |
| PSRAM no habilitada | `camera.cpp` | Se detecta al arrancar y **se detiene** con el nombre exacto de la opción del IDE. Una corrida degradada en silencio invalidaría la comparación. |
| Buffer de 256 B de PubSubClient | `mode_mqtt.cpp` | `setBufferSize(2048)` para telemetría, y la imagen va por `beginPublish()`/`write()`/`endPublish()`, que escribe **directo al socket** sin pasar por el buffer. |
| **`buildHeader()` usa `uint16_t`** | `mode_mqtt.cpp` | Límite que `setBufferSize()` **no** arregla: un payload de más de ~64 KB arma un paquete MQTT corrupto **sin devolver error**. Guarda explícita en `MQTT_MAX_PAYLOAD_BYTES`, se descarta y se avisa. Con VGA q12 no se llega, pero con SVGA/HD sí. |
| Cola del WebSocket revienta la RAM | `mode_websocket.cpp` | El `canSend()` de la librería permite **32** mensajes en cola: con frames VGA son ~1 MB de heap interno. Política propia: **como mucho un frame en vuelo**; si el anterior no drenó, el nuevo se descarta y se cuenta. En un stream en vivo el frame viejo no sirve igual. |
| Watchdog / hambruna de CPU | `camara_redes.ino`, `mode_http_mjpeg.cpp` | `delay(1)` al final del loop y en toda espera acotada. Nada bloquea sin ceder. |
| Reconexión WiFi | `net.cpp` | Backoff exponencial 1 s → 30 s, sin bloquear. `setAutoReconnect(false)` para que los reintentos sean los nuestros y no los del core. |
| Reconexión MQTT | `mode_mqtt.cpp` | Mismo backoff. `setSocketTimeout(3)` acota lo que puede tardar un intento contra un broker caído. |
| Power save falsea los tiempos | `net.cpp` | `WiFi.setSleep(false)`: con modem sleep el AP bufferea y entrega a ráfagas, y los tiempos entre paquetes dejarían de reflejar el ritmo del emisor. |
| Nagle junta escrituras | `mode_http_mjpeg.cpp` | `setNoDelay(true)`: cada `write()` sale cuando lo pedimos, así la temporización de los paquetes es la del emisor. |
| `build_src_filter` y el `.ino` | `platformio.ini` | PlatformIO convierte el `.ino` en `<nombre>.ino.cpp` **antes** de aplicar el filtro. Un filtro explícito tiene que pedir `+<*.ino.cpp>`; con `+<*.ino>` no entra nada y el link falla con `undefined reference to setup()`. |
| Cliente que no drena traba el loop | `mode_http_mjpeg.cpp` | Un cliente que abre el stream y **no lee** llena la ventana TCP y deja a `write()` esperando. `HTTP_SEND_TIMEOUT_MS` acota el bloqueo: al vencer sale una escritura corta, se cierra ese stream y el equipo se recupera solo. |
| **Atribuir mal los descartes** | `mode_http_mjpeg.cpp` | Cuando el consumidor no drena, TCP frena al emisor y las ranuras perdidas caían en `sched`, como si el planificador llegara tarde. Ahora, si una escritura tarda más que una ranura, las perdidas se cargan a `bp` y el reloj se resincroniza para no contarlas dos veces. Sin esto el desglose por causa miente justo cuando más se lo necesita. |
| Aceptar conexiones bloqueaba el loop | `mode_http_mjpeg.cpp` | Leer la request line con un read bloqueante costaba hasta 1 s por conexión. Con un cliente no se notaba; con dos peleándose el stream (una pestaña vieja reintentando) se perdían ranuras sin explicación. Ahora la request se lee de a pedazos a lo largo de varias vueltas y **el accept nunca bloquea** (`max_acc` 0-3 ms medido). |
| Paquetitos de 2 bytes por frame | `mode_http_mjpeg.cpp` | El CRLF de cierre de parte iba en un `write()` propio y, con `TCP_NODELAY`, salía como paquete de 2 bytes. Ahora va al principio de la cabecera de la parte siguiente: 2 escrituras por frame en vez de 3. |
| **El CSV frenaba el stream** | `camara_redes.ino`, `config.h` | El USB CDC nativo del S3 bloquea hasta 2 s por `write()` si nadie drena el puerto, así que la fila de CSV por frame estrangulaba el stream: 0,40 fps con el monitor cerrado contra 9,71 fps con el monitor abierto, misma red y mismo instante. `Serial.setTxTimeoutMs(SERIAL_TX_TIMEOUT_MS)` con 0 lo hace no bloqueante. El instrumento no puede frenar lo que mide. |
| Publicar desde el callback MQTT | `mode_mqtt.cpp` | El callback solo levanta una bandera. PubSubClient no es reentrante: publicar ahí corrompe el parseo del paquete en curso. |

---

## 13. El mismo instrumento en una PC (sin la placa)

Todo el equipo puede trabajar en paralelo con una sola placa:
[`pc/camara_pc.py`](pc/README.md) es el port del firmware a Python, con un
archivo espejo por cada archivo de acá (`config.py` ↔ `config.h`,
`mode_mqtt.py` ↔ `mode_mqtt.cpp`, …).

```bash
cd pc
python3 -m pip install -r requirements.txt     # solo para la webcam real
python3 camara_pc.py --modo 1                  # o --modo 2 / --modo 3
python3 camara_pc.py --fuente sintetica        # sin webcam, sin dependencias
```

Los bytes en el cable son los mismos, así que **los tres verificadores de
`tools/` sirven sin cambios** contra la PC y contra la placa. Lo que cambia, y
está documentado en [`pc/README.md`](pc/README.md) §6: `heap_free` y `rssi` no
tienen análogo en una PC (salen `-1`), y la calidad JPEG se iguala **por bytes**
con `--calibrar`, porque la escala de OpenCV es la inversa de la de la OV2640.

**Antes de medir en una PC, leer la §3 de ese README.** Si el emisor y el
receptor son la misma máquina, el tráfico sale por loopback y la segmentación
TCP no es comparable con la de la placa por WiFi.

---

## 14. Archivos

```
camara_redes/
├── platformio.ini         3 environments, uno por modo (VS Code)
├── camara_redes.ino       setup/loop; el mismo para los tres modos
├── config.h               TODA la configuración
├── camera_pins.h          pinout XIAO ESP32S3 (copia del oficial)
├── camera.h/.cpp          init de la OV2640
├── net.h/.cpp             WiFi + reconexión con backoff
├── metrics.h/.cpp         struct de métricas, CSV, JSON de telemetría
├── frame_clock.h          reloj de ranuras a FPS fijo + conteo de descartes
├── fb_guard.h             RAII sobre el frame buffer
├── modes.h                interfaz común de los tres modos
├── mode_http_mjpeg.cpp    modo 1
├── mode_websocket.cpp     modo 2
├── mode_mqtt.cpp          modo 3
├── tools/
│   ├── verificar_mjpeg.py valida el multipart del modo 1 desde la red
│   ├── verificar_ws.py    valida el handshake y los frames del modo 2
│   ├── verificar_mqtt.py  valida el diálogo MQTT y la telemetría del modo 3
│   ├── mosquitto.conf     broker de prueba
│   └── compilar.sh        compila los tres modos
└── pc/                    el mismo instrumento en Python, para trabajar sin la
                           placa (ver pc/README.md)
```

Los tres verificadores son stdlib pura: no hay nada que instalar para usarlos.
`verificar_mjpeg.py --autotest` se prueba a sí mismo sin hardware.
