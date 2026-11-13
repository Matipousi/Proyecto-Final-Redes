# Tutorial: correr la cámara en Windows

Guía paso a paso para correr el instrumento de medición en **tu computadora con
Windows y tu webcam**, sin la placa ESP32. Habla los mismos tres protocolos que
la placa (HTTP MJPEG, WebSocket y MQTT), con los mismos puertos y los mismos
bytes en la red, así que tus capturas de Wireshark se pueden comparar con las
de todo el equipo.

**Tiempo estimado:** 15-20 minutos la primera vez.

> Probado en macOS. En una máquina Windows real **todavía no se probó**: si algo
> de esta guía no coincide con lo que ves, avisá en el grupo para corregirla.

---

## Qué vas a tener al final

- Tu webcam transmitiendo en `http://localhost/` desde el navegador.
- Un comando para cada uno de los tres modos.
- Un archivo CSV por corrida, con el mismo formato que el de la placa.

---

## Paso 1 — Instalar Python

1. Entrá a <https://www.python.org/downloads/windows/> y bajá **Python 3.13**
   (*Windows installer (64-bit)*). Sirve cualquier versión desde la 3.11;
   las más viejas tienen un temporizador impreciso en Windows que desordena el
   ritmo de los frames.
2. Abrí el instalador y, **antes de tocar "Install Now"**, marcá la casilla
   **"Add python.exe to PATH"** abajo de todo. Es el error más común: sin eso,
   nada de lo que sigue funciona.
3. Instalá con las opciones por defecto.

Para comprobarlo, abrí **PowerShell** (tecla Windows, escribí `powershell`,
Enter) y escribí:

```powershell
py --version
```

Tiene que responder algo como `Python 3.13.x`.

---

## Paso 2 — Descargar el proyecto

El repositorio es **privado**: necesitás que te agreguen como colaborador en
GitHub. Después elegí una de estas dos formas:

**Opción A, más simple:** en la página del repositorio, botón verde **Code →
Download ZIP**. Descomprimilo, por ejemplo en `Documentos\Proyecto-Final-Redes`.

**Opción B, con Git** (si ya lo tenés instalado), para después poder bajar los
cambios con `git pull`:

```powershell
cd $HOME\Documents
git clone https://github.com/Matipousi/Proyecto-Final-Redes.git
```

---

## Paso 3 — Abrir PowerShell en la carpeta `pc`

1. En el Explorador de archivos, entrá a la carpeta del proyecto y después a
   **`pc`**.
2. Hacé clic en la **barra de direcciones** (donde dice la ruta), escribí
   `powershell` y apretá Enter.

Se abre una PowerShell ya ubicada en esa carpeta. Todos los comandos que siguen
se escriben ahí.

---

## Paso 4 — Instalar OpenCV en un entorno virtual

El entorno virtual (`.venv`) deja todo lo del proyecto adentro de su carpeta,
sin tocar el resto de tu Python. Copiá y pegá estas dos líneas:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

La segunda baja **OpenCV** (~40 MB), lo único que hace falta para leer la
webcam. Puede tardar unos minutos.

> **No hace falta "activar" el entorno.** En todos los comandos de esta guía se
> usa `.venv\Scripts\python` directamente. Así se evita el error de PowerShell
> *"la ejecución de scripts está deshabilitada en este sistema"*, que aparece
> si intentás correr `Activate.ps1`.

---

## Paso 5 — Primera prueba, sin cámara

Antes de meter la webcam, probá que el transporte funciona con frames
generados:

```powershell
.venv\Scripts\python camara_pc.py --fuente sintetica
```

1. Va a aparecer un cartel del **Firewall de Windows** preguntando si permitís
   que Python acceda a la red. Marcá **"Redes privadas"** y tocá **Permitir
   acceso**. Si lo cancelás, desde otra computadora no se va a poder conectar.
2. Abrí el navegador en **<http://localhost/>**. Tenés que ver un patrón de
   colores en movimiento con la leyenda `SINTETICO` y un número que avanza.
3. Para cortar, volvé a la PowerShell y apretá **Ctrl+C**.

Si llegaste hasta acá, la parte de red ya funciona.

---

## Paso 6 — Habilitar la cámara

Windows bloquea la cámara para las aplicaciones de escritorio salvo que se lo
permitas:

1. **Configuración → Privacidad y seguridad → Cámara.**
2. Activá **"Acceso a la cámara"**.
3. Activá **"Permitir que las aplicaciones de escritorio accedan a la cámara"**
   (está abajo de todo de esa pantalla).

Cerrá también Teams, Zoom, Discord, OBS o cualquier programa que esté usando la
cámara: en Windows **dos programas no pueden usarla al mismo tiempo**.

---

## Paso 7 — Correr con la webcam

```powershell
.venv\Scripts\python camara_pc.py
```

Abrí **<http://localhost/>**: ahora tenés que verte a vos. Para cortar,
**Ctrl+C**.

Si tenés más de una cámara (la de la notebook y una USB, por ejemplo) y abre la
que no querés:

```powershell
.venv\Scripts\python camara_pc.py --camara 1
```

---

## Paso 8 — Calibrar (una sola vez, y todos el mismo valor)

La placa y tu PC comprimen el JPEG con codificadores distintos, así que la
"calidad" no significa lo mismo en las dos. Para que las capturas sean
comparables hay que igualar los **bytes por frame**, no el número de calidad:

```powershell
.venv\Scripts\python camara_pc.py --calibrar
```

Al final te dice algo como:

```
mas cercano al ESP32: calidad 68
```

Abrí `pc\config.py` con el Bloc de notas y cambiá esta línea por ese valor:

```python
CAM_JPEG_QUALITY_CV = 68
```

**Pónganse de acuerdo en el equipo y usen todos el mismo valor.** Si cada uno
usa uno distinto, las capturas no se pueden comparar entre sí.

---

## Paso 9 — Los tres modos

| Modo | Comando | Qué abrir |
|---|---|---|
| 1 · HTTP MJPEG | `.venv\Scripts\python camara_pc.py --modo 1` | <http://localhost/> |
| 2 · WebSocket | `.venv\Scripts\python camara_pc.py --modo 2` | <http://localhost/> |
| 3 · MQTT | `.venv\Scripts\python camara_pc.py --modo 3 --broker 127.0.0.1` | ver Paso 10 |

En los modos 1 y 2 tu PC es el **servidor**: el navegador se conecta a ella.
En el modo 3 tu PC es el **cliente**: es ella la que sale hacia el broker MQTT y
no abre ningún puerto. Esa inversión es justamente lo que se compara en el
trabajo.

---

## Paso 10 — Mosquitto (solo para el modo 3)

1. Bajá el instalador de **Windows 64-bit** desde
   <https://mosquitto.org/download/> e instalalo con las opciones por defecto.
2. El instalador deja Mosquitto corriendo como servicio, configurado para
   escuchar **solo en tu propia máquina**. Hay que frenarlo para usar la
   configuración del proyecto. Abrí una PowerShell **como administrador** (clic
   derecho en el ícono → *Ejecutar como administrador*) y escribí:

   ```powershell
   net stop mosquitto
   ```

3. En una PowerShell común, parada en la carpeta `pc`, arrancalo con la
   configuración del proyecto:

   ```powershell
   & "C:\Program Files\mosquitto\mosquitto.exe" -c ..\tools\mosquitto.conf -v
   ```

   Si aparece el cartel del Firewall, permití el acceso en **Redes privadas**.
   Dejá esa ventana abierta: ahí vas a ver los mensajes MQTT pasar.

4. En **otra** PowerShell, también en `pc`:

   ```powershell
   .venv\Scripts\python camara_pc.py --modo 3 --broker 127.0.0.1
   ```

5. Para pedirle una foto o prender el video continuo, desde una tercera
   PowerShell:

   ```powershell
   & "C:\Program Files\mosquitto\mosquitto_pub.exe" -h 127.0.0.1 -t camara/cmd -m snapshot
   & "C:\Program Files\mosquitto\mosquitto_pub.exe" -h 127.0.0.1 -t camara/cmd -m stream:on
   ```

---

## Paso 11 — Grabar una corrida

```powershell
.venv\Scripts\python camara_pc.py --modo 1 --duracion 60 --csv modo1.csv
```

Corta sola a los 60 segundos y deja `modo1.csv` en la carpeta `pc`.

> **Usá `--csv`, no `>`.** En la PowerShell que trae Windows,
> `... > modo1.csv` graba el archivo en UTF-16 y después pandas o Excel lo leen
> como basura. `--csv` lo graba bien.

Para leerlo en Python:

```python
import pandas as pd
df = pd.read_csv("modo1.csv", comment="#")
```

---

## Paso 12 — Verificar que todo salió bien

Mientras la cámara está corriendo, desde otra PowerShell en `pc`:

```powershell
.venv\Scripts\python ..\tools\verificar_mjpeg.py 127.0.0.1               # modo 1
.venv\Scripts\python ..\tools\verificar_ws.py    127.0.0.1 --puerto 80   # modo 2
.venv\Scripts\python ..\tools\verificar_mqtt.py  127.0.0.1 --snapshot    # modo 3
```

Cada uno termina en una línea que empieza con **`OK:`** o con **`FALLO:`** y el
motivo.

---

## Paso 13 — Capturar con Wireshark

1. Instalá Wireshark desde <https://www.wireshark.org/download.html>. Cuando
   pregunte, **dejá marcado Npcap** y, dentro de sus opciones, marcá **"Support
   loopback traffic"**.
2. **Si el navegador y la cámara están en la misma PC**, capturá en la interfaz
   **"Adapter for loopback traffic capture"**: el tráfico a `localhost` no pasa
   por tu WiFi ni por tu placa de red.
3. En el modo 1, el puerto 81 no se decodifica como HTTP solo: clic derecho
   sobre un paquete → **Decode As…** → puerto `81` → **HTTP**.

> **Importante para el informe:** en loopback los paquetes son mucho más grandes
> que en una red real, así que la cantidad y el tamaño de los segmentos TCP **no
> se pueden comparar** con los de la placa. El diálogo de los protocolos sí.
> Para medir segmentación, usá **dos computadoras en la misma red**: una corre la
> cámara y la otra abre `http://<ip-de-la-primera>/` y captura. La IP aparece en
> la primera línea `# http: UI en http://...` al arrancar. La explicación
> completa está en [`pc/README.md`](pc/README.md), sección 3.

---

## Problemas frecuentes

| Síntoma | Qué hacer |
|---|---|
| `py` no se reconoce como comando | No marcaste "Add python.exe to PATH". Volvé a correr el instalador → **Modify** → marcá "Add Python to environment variables". Después cerrá y abrí PowerShell. |
| `ERROR: no se pudo abrir la camara 0` | Revisá el Paso 6 (permisos), cerrá Teams/Zoom/OBS, o probá `--camara 1`. |
| `aviso: no pude atar el puerto 80 ... uso el 8080` | Otro programa usa el puerto 80 (suele ser IIS o algún servicio de Windows). No pasa nada: abrí **<http://localhost:8080/>**, pero acordate de que en Wireshark el puerto también cambia. Para ver quién lo usa: `netstat -ano \| findstr :80` |
| Desde mi PC anda, pero desde otra computadora no | 1) El Firewall: buscá "Permitir una aplicación a través de Firewall de Windows" y habilitá `python.exe` en **Privada**. 2) Tu red puede estar marcada como **Pública**: Configuración → Red e Internet → tu WiFi → **Red privada**. |
| `la ejecución de scripts está deshabilitada` | Estás intentando activar el entorno. No hace falta: usá `.venv\Scripts\python ...` como en la guía. |
| `No module named 'cv2'` | Corriste `py` o `python` en vez de `.venv\Scripts\python`, o el Paso 4 no terminó bien. Repetí la segunda línea del Paso 4. |
| El CSV se ve con caracteres raros | Lo grabaste con `>`. Usá `--csv archivo.csv` (Paso 11). |
| El video se ve entrecortado y aparecen muchos `dropped` | Si la pestaña del navegador queda en segundo plano, el navegador la frena. Dejala visible, o medí con los verificadores del Paso 12. |

---

## Para cortar todo

**Ctrl+C** en cada PowerShell que tenga algo corriendo. Si usaste Mosquitto y
querés volver a dejar el servicio como estaba (PowerShell como administrador):

```powershell
net start mosquitto
```

---

## Más información

- [`pc/README.md`](pc/README.md): qué es igual y qué es distinto respecto de la
  placa, la trampa del loopback en detalle, y todas las opciones.
- [`README.md`](README.md): el manual completo del firmware y la tabla de qué
  esperar ver en Wireshark en cada modo.
