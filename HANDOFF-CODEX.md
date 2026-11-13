# Handoff prompt — camara_redes (XIAO ESP32S3 Sense)

> Paste this whole file as the opening prompt. The project lives at
> `~/Documents/Arduino/camara_redes`. Read `README.md` first — it is the spec and
> the lab manual, and it is kept current.

---

## 1. What you are taking over

Firmware for a **Seeed XIAO ESP32S3 Sense** (ESP32-S3R8, 8 MB OPI PSRAM, OV2640)
that streams the same JPEG video over **three transport architectures**, selected
at compile time. It is a university **Data Networks** assignment.

**The firmware is not the deliverable. It is a measuring instrument.** The
deliverable is a set of Wireshark captures plus a comparison of the three
transports.

### The one rule that overrides everything

> **Network behaviour must be legible in a packet capture. When performance and
> observability conflict, observability wins.**

Do not add anything that hides what happens on the wire: no frame batching, no
extra compression beyond the camera's JPEG, no reordering, no adaptive bitrate,
no connection multiplexing. Several things in this codebase look like missed
optimisations and are deliberate. They are marked in comments — read the comment
before "fixing" it.

### The three modes

| Mode | `STREAM_MODE` | Device role | Ports | Who opens the TCP connection |
|---|---|---|---|---|
| 1 | `MODE_HTTP_MJPEG` | **server** | listens on 80 (UI) + **81** (stream) | PC → ESP32 |
| 2 | `MODE_WEBSOCKET` | **server** | listens on 80 (UI + WS `/stream`) | PC → ESP32 |
| 3 | `MODE_MQTT` | **client** | **listens on nothing** | ESP32 → broker :1883 |

Mode 1 splits UI and stream across two ports on purpose, so the capture shows one
short connection to :80 and one permanent connection to :81. Mode 3's whole point
is the inverted connection direction.

---

## 2. Hard constraints — do not change these without being asked

- **No TLS.** The capture must be readable plaintext; that is the point of the
  assignment. Hooks are prepared and commented out at the end of `config.h`
  (`USE_TLS`, `MQTT_PORT_TLS`, `MQTT_CA_CERT`, …) and in `tools/mosquitto.conf`.
- **No authentication.** Same reason. Mosquitto runs with `allow_anonymous true`.
- **Everything configurable lives in `config.h`.** One file. Do not scatter
  constants into the mode files.
- **The instrumentation is identical across all three modes.** That is what makes
  the captures comparable. If you change what a metric means, change it in all
  three or you break the experiment.
- **Comments and docs are in Spanish** (no accents in source comments, accents
  are fine in Markdown). Match the surrounding style: comments explain *why*, not
  *what*.
- Target is **arduino-esp32 core 3.x**. 2.x lacks `WiFiServer::accept()`,
  `NetworkClient` and `setConnectionTimeout()`.

---

## 3. Toolchain and how to build

Two environments work on the same folder; `src_dir = .` keeps the Arduino sketch
layout intact.

### PlatformIO (primary, in VS Code)

```bash
pio run -e modo1_http_mjpeg -t upload      # mode selected by ENVIRONMENT
pio run -e modo2_websocket  -t upload
pio run -e modo3_mqtt       -t upload
pio device monitor                          # 115200
```

`pio` is at `~/.platformio/penv/bin/pio` (not on PATH).

- Platform is pinned to **pioarduino `55.03.311`** (= Arduino core **3.3.11**).
  The official `platformio/espressif32` 7.1.0 still ships core **2.0.17** and
  will not compile this. Do not "update" to the official platform.
- The board definition already sets `memory_type = qio_opi`, `-DBOARD_HAS_PSRAM`,
  `ARDUINO_USB_MODE=1`, `ARDUINO_USB_CDC_ON_BOOT=1`.
- `pio run` with no `-e` builds **only** the default env (mode 1). Name all three
  explicitly to check them all.
- **`build_src_filter` must list `+<*.ino.cpp>`, not `+<*.ino>`.** PlatformIO
  converts the sketch to `camara_redes.ino.cpp` *next to* the `.ino` before
  filtering, and deletes it on exit. With `+<*.ino>` nothing matches and the link
  fails with `undefined reference to setup()`. If an interrupted build leaves
  that file behind, the Arduino IDE will then compile both and fail on duplicate
  `setup()`/`loop()` — delete it (`rm -f camara_redes.ino.cpp`, already
  gitignored).

### Arduino IDE (secondary)

Board `XIAO_ESP32S3`. **PSRAM must be switched to `OPI PSRAM` manually — it
defaults to `Disabled`.** Mode is chosen by editing `STREAM_MODE` in `config.h`
(the `#ifndef` guard around it exists so PlatformIO can override it with
`-DSTREAM_MODE=n`). `tools/compilar.sh` builds all three via `arduino-cli`.

Libraries (mode 2 and 3 only; each `mode_*.cpp` is wrapped in `#if STREAM_MODE ==
…` so unused modes compile to empty translation units and need no libraries):
`esp32async/ESPAsyncWebServer@3.12.0`, `esp32async/AsyncTCP@3.5.0`,
`knolleary/PubSubClient@2.8`.

---

## 4. Code map

```
camara_redes.ino      setup/loop, same for all three modes
config.h              ALL configuration + the commented TLS/auth hooks
camera_pins.h         XIAO pinout, verbatim copy of the core's example header
camera.h/.cpp         OV2640 init (PSRAM, GRAB_LATEST, fb_count 2)
net.h/.cpp            WiFi + non-blocking reconnect with backoff
metrics.h/.cpp        the shared metrics struct, CSV rows, telemetry JSON
frame_clock.h         fixed-FPS slot clock; missed slots become drops
fb_guard.h            RAII wrapper over camera_fb_t
modes.h               streamModeSetup() / streamModeLoop() / streamModeName()
mode_http_mjpeg.cpp   mode 1
mode_websocket.cpp    mode 2
mode_mqtt.cpp         mode 3
tools/verificar_mjpeg.py   validates mode 1 from the network (has --autotest)
tools/verificar_ws.py      validates mode 2 from the network
tools/verificar_mqtt.py    validates mode 3 against the broker (stdlib MQTT client)
tools/mosquitto.conf       test broker
tools/compilar.sh          builds all three via arduino-cli
pc/                        the same instrument in Python, for working without
                           the board. One file mirrors each file above; see
                           pc/README.md. Same wire bytes, so all three
                           verifiers work unchanged against it.
```

Each mode file defines `streamModeSetup()` / `streamModeLoop()` /
`streamModeName()` inside its own `#if`, so exactly one implementation exists per
build.

### The instrumentation contract

Serial emits **CSV, one row per frame actually sent**, with a header:

```
ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped
```

Everything that is not a data row is prefixed with `#`, so the dump is a valid
CSV (`pandas.read_csv(..., comment="#")`).

- `dropped` is a **cumulative column**, not a row type. A dropped frame produces
  no row; the next row shows the counter incremented.
- `send_ms` means **"time until the frame was handed to the network"** in all
  three modes, but is obtained differently. Mode 1: the blocking socket write.
  Mode 2: `binary()` is async and returns immediately, so the CSV row is
  **deferred** until the WS queue drains, and `send_ms` is enqueue→drained. Mode
  3: `beginPublish()/write()/endPublish()`. Keep this equivalence if you touch
  any of them, or the comparison becomes meaningless.
- `drop_detail` in the telemetry JSON breaks drops down by cause: `sched`, `cap`,
  `bp` (backpressure), `noclient`, `tx`. **Correct attribution matters more than
  the total** — it is the field used to decide who is at fault.
- Modes 1 and 2 expose the same JSON at `GET /status`; mode 3 publishes it to
  `camara/telemetria` at 1 Hz.

---

## 5. Verification status — read before you trust anything

### Firmware (the board)

| | Compiles | Runs on hardware | End-to-end verified |
|---|---|---|---|
| Mode 1 HTTP MJPEG | yes | **yes** | **yes** |
| Mode 2 WebSocket | yes | **no** | **no** |
| Mode 3 MQTT | yes | **no** | **no** |

**Modes 2 and 3 have never executed on the board.** They are compile-clean and
were written against verified library APIs, but treat every runtime claim about
them as unproven.

### PC port (`pc/`)

| | Runs | End-to-end verified | How |
|---|---|---|---|
| Mode 1 HTTP MJPEG | yes | **yes** | `verificar_mjpeg.py`: 10.16 fps, 1627 kbit/s, JPEG 18-21 KB, 0 drops, `X-Slot` contiguous, 0 ms latency drift |
| Mode 2 WebSocket | yes | **yes** | `verificar_ws.py`: correct `101` + `Sec-WebSocket-Accept`, one JPEG per binary message, 0 fragmented, 0 ping/pong, 0 masked |
| Mode 3 MQTT | yes | **yes** | `verificar_mqtt.py` against **real Mosquitto 2.1.2**: `CONNECT (p4, c1, k15)`, `CONNACK`, `SUBSCRIBE`/`SUBACK`, 1.00/s telemetry, `snapshot` returns an intact JPEG, QoS 0, `PINGREQ`/`PINGRESP` at ~15 s |

Verified on macOS with `--fuente sintetica` over **loopback**. Two caveats:

1. **The real-webcam path (`WebcamSource` in `pc/camera.py`) has never run** —
   no camera permission and no OpenCV in that environment. It is written against
   the OpenCV API but unexecuted. Test it first.
2. Loopback distorts TCP segmentation (16 KB+ MTU), so `send_ms` came out at
   0-1 ms against the board's 28 ms. Protocol-level behaviour is comparable;
   segmentation is not. `pc/README.md` §3 covers this.

Cross-checks that passed: CSV `dropped` == network-side drop count; CSV header
identical column by column; `slot() == frames_sent + drops` under random
jitter; and **drop attribution** — a client reading at 3.9 KiB/s against
~200 KiB/s produced `bp: 18, sched: 0`, the same shape as the firmware's
verified reproduction (`bp: 169, sched: 0`).

### Measured baselines for mode 1 (use these to detect regressions)

Board at `192.168.4.84`, network `Maticueva 2.4G`, channel 10.

| Config | RSSI | fps | bitrate | drops | latency drift |
|---|---|---|---|---|---|
| `FRAMESIZE_VGA` q12, 10 fps target | −43 dBm | **10.04** | 1820 kbit/s | **0** | +0 ms |
| `FRAMESIZE_QVGA` q12, 10 fps target | −75 dBm | **10.05** | 668 kbit/s | **0** | +0 ms |

Typical per-frame numbers at VGA: `capture_ms` ~0-1 (median), `send_ms` median
~28 ms, JPEG ~17-23 KB.

Loop profile when healthy (`DEBUG_LOOP_TIMING 1`):
`vueltas/s≈500  max_ui=1-2  max_acc=0-3  max_send<slot`.

---

## 6. Traps already solved. Do not regress these.

Each has a comment at the site explaining why. Listed roughly by how expensive
they were to find.

1. **Serial over native USB CDC blocks and throttles the stream.** The S3's CDC
   `write()` waits up to `20 × 100 ms = 2 s` when nothing drains the port. With
   one CSV row per frame, the instrument was strangling the thing it measured:
   **0.40 fps with the monitor closed vs 9.71 fps with it open, same network,
   same instant.** Fixed by `Serial.setTxTimeoutMs(SERIAL_TX_TIMEOUT_MS)` with
   `SERIAL_TX_TIMEOUT_MS 0` (non-blocking; rows are dropped when the buffer is
   full). **Never make Serial output blocking again.**
   *Methodological consequence: when measuring anything, control for whether the
   serial monitor is attached. A confounded version of this cost hours and
   produced a completely wrong "it's the WiFi link / check the antenna"
   diagnosis.*
2. **Drop cause attribution.** When the consumer does not drain, TCP throttles
   the sender, and the missed slots used to be charged to `sched` as if the
   scheduler were late. Now a write longer than one slot charges the lost slots
   to `bp` and resyncs the clock so they are not counted twice. Verified by
   reproducing with a deliberately slow client: `bp: 169, sched: 0`.
3. **The accept path must never block.** Reading the request line with a blocking
   read cost up to 1 s per connection; with two clients fighting over the
   single-viewer stream (a stale browser tab retrying) it silently ate slots.
   Now the request is read in pieces across loop iterations
   (`progressPending()`), and `HTTP_ACCEPT_TIMEOUT_MS` is a wall-clock budget,
   not a wait. Measured `max_acc` 0-3 ms.
4. **`esp_camera_fb_return()` on every exit path.** Enforced structurally by
   `FbGuard` (RAII) instead of remembering it at each `return`. Missing one is
   the classic "hangs after N frames".
5. **PubSubClient has two size limits, not one.** `setBufferSize(2048)` fixes the
   256-byte default (the telemetry JSON does not fit in 256 and is discarded
   silently). But `buildHeader()` takes the length as **`uint16_t`**, so a
   payload over ~64 KB produces a corrupt MQTT packet **with no error**. Guarded
   by `MQTT_MAX_PAYLOAD_BYTES`. The image is published with
   `beginPublish()/write()/endPublish()`, which streams straight to the socket
   and never passes through the buffer.
6. **The async WebSocket queue will exhaust internal RAM.** The library's
   `canSend()` allows up to `WS_MAX_QUEUED_MESSAGES` (32) queued messages; at
   ~20-30 KB per VGA frame that is ~1 MB of internal heap, and `binary()`
   *copies*. Policy: **at most one frame in flight** — if the previous one has
   not drained, drop the new one and count it. A stale frame is worthless in a
   live stream anyway.
7. **`WiFiClient` is a typedef of `NetworkClient` and its `operator bool()` IS
   `connected()`.** So `if (client)` cannot detect a disconnect transition; an
   explicit `s_stream_active` flag is required, or the socket leaks.
8. **WiFi retry must not fire while an attempt is in flight.** Association plus
   DHCP takes 2-6 s; retrying sooner makes the driver reject `WiFi.begin()` with
   `sta is connecting, cannot set config`, which also pollutes the CSV (no `#`
   prefix). Each attempt gets `WIFI_ATTEMPT_TIMEOUT_MS`; backoff is added *after*
   that window.
9. **ESP-IDF logs corrupt the CSV** (no `#` prefix). Suppressed via
   `esp_log_level_set("*", ESP_LOG_NONE)` behind `SUPPRESS_IDF_LOGS`.
10. **`WiFi.setSleep(false)`** — with modem sleep the AP buffers and bursts,
    so inter-packet timing would stop reflecting the sender. Costs power, buys
    observability.
11. **`setNoDelay(true)` in mode 1**, so each `write()` reaches the wire when we
    ask. The part-closing CRLF was merged into the *next* part header, giving 2
    writes per frame instead of 3 (the third was a 2-byte packet). The
    `\r\n--boundary\r\n` leading form matches Espressif's official
    `CameraWebServer` — it is correct, do not "fix" it.
12. **The MQTT callback only raises a flag.** PubSubClient is not reentrant;
    publishing from inside the callback corrupts the packet being parsed.
13. **Single viewer per stream, second gets 503.** With two viewers the bandwidth
    splits and the metrics stop meaning what they say. Explicit rejection is
    visible in the capture; silent degradation is not.

---

## 7. How to tell whether a bad run is the firmware or the network

`README.md` §10 documents this as a procedure. Short version:

1. `drop_detail` in `/status`: `bp` high ⇒ the consumer is not draining (a
   backgrounded browser tab throttles; use the verifier scripts instead).
   `sched` high with `bp` at 0 ⇒ something is blocking the loop.
2. Set `DEBUG_LOOP_TIMING 1` and read the per-second profile line. It reports the
   max time in each phase plus the accept state machine. Turn it back to **0**
   for measurement runs — it adds a `#` line per second.
3. **Open and close the serial monitor and compare.** If the result changes, it
   is instrumentation, not transport. (See trap 1.)
4. Only then blame the link. Check the bitrate budget: `bytes_per_frame ×
   TARGET_FPS × 8` must fit comfortably in what the link actually delivers. VGA
   q12 ≈ 1.4-1.8 Mbps, QVGA q12 ≈ 0.4-0.7 Mbps. QVGA reached the target even at
   −75 dBm, so poor RSSI alone does not explain a low FPS.

---

## 8. Suggested next steps

In rough priority order. Confirm with the user before reordering.

1. **Bring up mode 2 on hardware.** Flash `modo2_websocket`, open
   `http://<ip>/`, and run `python3 tools/verificar_ws.py <ip>`. The verifier
   checks `Sec-WebSocket-Accept`, one complete JPEG per binary message
   (`opcode 0x2`, `FIN=1`), no server-side masking, and no ping/pong frames.
   Pay attention to the deferred-CSV logic (`s_pending` + `queueLen() == 0`) —
   it has never executed.
2. **Bring up mode 3 on hardware.** Needs Mosquitto (`brew install mosquitto`,
   **not yet installed**) running as `mosquitto -c tools/mosquitto.conf -v` on
   the **same machine that runs Wireshark** — the network is switched, so a
   broker elsewhere means the capture will not see the ESP32↔broker traffic.
   `MQTT_BROKER_IP` is already set to the Mac (`192.168.4.58`). Verify: CONNECT/
   CONNACK, SUBSCRIBE/SUBACK, 1 Hz telemetry, `snapshot` command, and
   `stream:on` for the continuous-video comparison.
3. ~~**Add a mode 3 verifier**~~ — **done**. `tools/verificar_mqtt.py` exists:
   stdlib-only MQTT 3.1.1 client, validates the telemetry JSON schema, JPEG
   integrity on `camara/imagen`, QoS 0, and the telemetry period. Verified
   against real Mosquitto using the PC port; **not yet run against the board**.
4. **Run the three comparison captures** with identical parameters (same
   framesize, quality and `TARGET_FPS`) and cross-check each CSV against its
   verifier output. A run is only trustworthy when the firmware's `dropped` and
   the network-side drop count agree.
5. **Test the real-webcam path of the PC port.** `pc/camera.py` `WebcamSource`
   is the only unexecuted code there. Then run `python3 camara_pc.py --calibrar`
   to pick the OpenCV quality that matches the board's 17-23 KB frames, and use
   that same value on every team machine or the runs will not be comparable.
6. **Capture the PC port between two machines**, not on loopback, so TCP
   segmentation is comparable with the board's (`pc/README.md` §3).
7. Optional: teach `verificar_mjpeg.py` to also cross-check the CSV file
   directly, so the two sources are compared automatically rather than by eye.

---

## 9. Working agreement

- **Verify on hardware, not just by compiling.** This project has already
  produced two confident, wrong, compile-clean diagnoses. When you claim
  something works, say how you checked it.
- **Change one variable at a time and control the obvious confounder** (serial
  monitor attached or not). Chasing symptoms across several simultaneous edits is
  exactly how trap 1 stayed hidden.
- After touching any shared file (`config.h`, `metrics.*`, `net.*`, the `.ino`),
  rebuild **all three** modes:
  `pio run -e modo1_http_mjpeg -e modo2_websocket -e modo3_mqtt`.
- Keep `README.md` current — it is the lab manual the user submits alongside the
  captures. If you find a diagnosis in it to be wrong, correct it there rather
  than only in chat.
- WiFi credentials live in `config_local.h` (gitignored; template in
  `config_local.example.h`). `config.h` includes it via `__has_include` and
  falls back to `"CAMBIAME"`. Never print, commit or paste them.
- The project is **not** a git repository yet. Consider offering to `git init`
  before large refactors so changes are revertible.
