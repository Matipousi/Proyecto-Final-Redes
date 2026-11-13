#include "config.h"
#if STREAM_MODE == MODE_MQTT

// =============================================================================
//  MODO 3  -  MQTT. El equipo es CLIENTE.
//
//  Diferencia estructural con los modos 1 y 2: el ESP32 NO ABRE NINGUN PUERTO.
//  Es el la placa la que sale hacia el broker. En la captura, el SYN va del
//  ESP32 al broker (puerto destino 1883), al reves que en los otros dos modos.
//  Esa inversion de la direccion de la conexion es el punto del modo.
//
//  Topicos:
//    camara/telemetria  (pub, 1 Hz)  JSON con fps, bytes/s, RSSI, heap, drops...
//    camara/cmd         (sub)        "snapshot" | "stream:on" | "stream:off"
//    camara/imagen      (pub)        el JPEG crudo
//
//  Sin TLS y sin usuario/password a proposito: 1883 en claro para poder leer los
//  paquetes MQTT en Wireshark (el disector los decodifica solo).
// =============================================================================
#include <PubSubClient.h>
#include <WiFi.h>

#include "camera.h"
#include "fb_guard.h"
#include "frame_clock.h"
#include "metrics.h"
#include "modes.h"
#include "net.h"

static WiFiClient   s_tcp;
static PubSubClient s_mqtt(s_tcp);
static FrameClock   s_clock;

static uint32_t s_next_retry_ms = 0;
static uint32_t s_backoff_ms    = MQTT_RETRY_BASE_MS;
static uint32_t s_last_window   = 0;

// Escritos desde el callback de PubSubClient: solo se marcan banderas, nunca se
// publica desde adentro del callback (PubSubClient no es reentrante: publicar
// ahi corrompe el parseo del paquete que se esta procesando).
static volatile bool s_snapshot_req = false;
static bool          s_streaming    = (MQTT_STREAM_ENABLED != 0);

// -----------------------------------------------------------------------------
// Comandos entrantes
// -----------------------------------------------------------------------------
static void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  char cmd[32];
  unsigned int n = (len < sizeof(cmd) - 1) ? len : sizeof(cmd) - 1;
  memcpy(cmd, payload, n);
  cmd[n] = 0;

  if (strcmp(cmd, "snapshot") == 0) {
    s_snapshot_req = true;                      // se atiende en el loop
  } else if (strcmp(cmd, "stream:on") == 0) {
    s_streaming = true;
    s_clock.resync();
    logComment("mqtt: stream continuo ON");
  } else if (strcmp(cmd, "stream:off") == 0) {
    s_streaming = false;
    logComment("mqtt: stream continuo OFF");
  } else {
    logComment("mqtt: comando desconocido en %s: \"%s\"", topic, cmd);
  }
}

// -----------------------------------------------------------------------------
// Conexion al broker, con backoff
// -----------------------------------------------------------------------------
static void mqttEnsureConnected() {
  if (s_mqtt.connected()) return;

  uint32_t now = millis();
  if ((int32_t)(now - s_next_retry_ms) < 0) return;   // todavia no toca

  logComment("mqtt: conectando a %s:%d (backoff %lu ms)",
             MQTT_BROKER_IP, (int)MQTT_PORT, (unsigned long)s_backoff_ms);

  if (s_mqtt.connect(MQTT_CLIENT_ID)) {
    s_backoff_ms = MQTT_RETRY_BASE_MS;                // reset del backoff
    s_mqtt.subscribe(MQTT_TOPIC_CMD, MQTT_QOS);
    s_clock.resync();
    logComment("mqtt: CONNACK ok, suscripto a %s", MQTT_TOPIC_CMD);
  } else {
    logComment("mqtt: fallo (state=%d)", s_mqtt.state());
    s_backoff_ms = (s_backoff_ms * 2 > MQTT_RETRY_MAX_MS) ? MQTT_RETRY_MAX_MS
                                                          : s_backoff_ms * 2;
  }
  s_next_retry_ms = millis() + s_backoff_ms;
}

// -----------------------------------------------------------------------------
// Publicacion de un JPEG
// -----------------------------------------------------------------------------
// No se usa publish(): eso exigiria un buffer del tamanio del frame en RAM
// interna. beginPublish() escribe la cabecera MQTT anunciando la longitud y
// despues el payload va directo al socket, sin copia intermedia. Es lo que
// permite publicar un JPEG de decenas de KB con un buffer de 2 KB.
static bool publishFrame(const char* topic, bool count_metrics) {
  uint32_t t0 = millis();
  FbGuard  fb(esp_camera_fb_get());
  uint32_t capture_ms = millis() - t0;

  if (!fb) {
    if (count_metrics) metricsFrameDropped(DROP_CAPTURE);
    return false;
  }

  // Segundo limite de PubSubClient, este NO lo arregla setBufferSize():
  // buildHeader() recibe la longitud como uint16_t, asi que un payload de mas de
  // ~64 KB desborda y arma un paquete MQTT corrupto SIN devolver error. Con VGA
  // q12 un frame ronda 20-50 KB y no se llega, pero subiendo a SVGA/HD o bajando
  // el numero de calidad si. Se descarta y se avisa, antes que emitir basura.
  if (fb->len > MQTT_MAX_PAYLOAD_BYTES) {
    if (count_metrics) metricsFrameDropped(DROP_TRANSPORT);
    logComment("mqtt: frame de %u B supera el limite de %u B de PubSubClient; "
               "bajar CAM_FRAMESIZE o subir CAM_JPEG_QUALITY",
               (unsigned)fb->len, (unsigned)MQTT_MAX_PAYLOAD_BYTES);
    return false;
  }

  uint32_t t1 = millis();
  if (!s_mqtt.beginPublish(topic, fb->len, false)) {
    if (count_metrics) metricsFrameDropped(DROP_TRANSPORT);
    return false;                       // el fb se devuelve igual (RAII)
  }
  size_t w  = s_mqtt.write(fb->buf, fb->len);
  bool   ok = s_mqtt.endPublish() && (w == fb->len);
  uint32_t send_ms = millis() - t1;

  if (!ok) {
    if (count_metrics) metricsFrameDropped(DROP_TRANSPORT);
    logComment("mqtt: publish incompleto (%u/%u B)", (unsigned)w, (unsigned)fb->len);
    return false;
  }

  if (count_metrics) metricsFrameSent(fb->len, capture_ms, send_ms);
  return true;
}

// -----------------------------------------------------------------------------
// Telemetria 1 Hz
// -----------------------------------------------------------------------------
static void publishTelemetry() {
  char json[384];
  size_t n = metricsToJson(json, sizeof(json));

  // El JSON no entra en los 256 B por defecto de PubSubClient: sin
  // setBufferSize() esto se descartaria sin avisar. El chequeo queda explicito
  // para que, si algun dia crece el JSON, se vea en el log y no en el silencio.
  if (n + strlen(MQTT_TOPIC_TELEMETRY) + 8 > MQTT_BUFFER_BYTES) {
    logComment("mqtt: telemetria no entra en el buffer (%u B), subir MQTT_BUFFER_BYTES",
               (unsigned)n);
    return;
  }
  if (!s_mqtt.publish(MQTT_TOPIC_TELEMETRY, json)) {
    logComment("mqtt: fallo publish de telemetria");
  }
}

// -----------------------------------------------------------------------------
// Interfaz de modo
// -----------------------------------------------------------------------------
const char* streamModeName() { return "MODE_MQTT (cliente, sale hacia el broker :1883)"; }

void streamModeSetup() {
  s_mqtt.setServer(MQTT_BROKER_IP, MQTT_PORT);
  s_mqtt.setCallback(onMqttMessage);

  // LA trampa de PubSubClient: 256 B por defecto y descarte silencioso.
  if (!s_mqtt.setBufferSize(MQTT_BUFFER_BYTES)) {
    logComment("mqtt: ERROR, no se pudo reservar el buffer de %d B", (int)MQTT_BUFFER_BYTES);
  }
  // Timeout de socket corto: un intento contra un broker caido bloquea como
  // mucho esto, y despues el backoff separa los reintentos.
  s_mqtt.setSocketTimeout(3);
  s_mqtt.setKeepAlive(15);      // PINGREQ/PINGRESP cada 15 s, visibles en la captura

  s_clock.begin(FRAME_INTERVAL_MS);
  s_last_window = g_m.window_seq;

  logComment("mqtt: broker %s:%d, sin TLS ni auth (a proposito)", MQTT_BROKER_IP, (int)MQTT_PORT);
  logComment("mqtt: pub %s (1 Hz) / pub %s / sub %s",
             MQTT_TOPIC_TELEMETRY, MQTT_TOPIC_IMAGE, MQTT_TOPIC_CMD);
  logComment("mqtt: stream continuo %s", s_streaming ? "ON" : "OFF (solo snapshot)");
  logComment("mqtt: el equipo NO escucha en ningun puerto");
}

void streamModeLoop() {
  mqttEnsureConnected();

  if (!s_mqtt.connected()) {
    s_clock.resync();          // sin broker no se acumulan descartes por ranura
    return;
  }

  s_mqtt.loop();               // procesa entrantes y manda el PINGREQ del keepalive

  // Telemetria: exactamente una por ventana cerrada, sin abrir un timer propio
  // que se desfase del que calcula fps y bytes/s.
  if (g_m.window_seq != s_last_window) {
    s_last_window = g_m.window_seq;
    publishTelemetry();
  }

  // Snapshot bajo demanda. Se publica aca, fuera del callback.
  if (s_snapshot_req) {
    s_snapshot_req = false;
    logComment("mqtt: snapshot pedido por %s", MQTT_TOPIC_CMD);
    // No cuenta en las metricas de stream: es un evento aparte y contaminaria
    // el fps de la ventana.
    publishFrame(MQTT_TOPIC_IMAGE, false);
  }

  if (!s_streaming) {
    s_clock.resync();
    return;
  }

  if (s_clock.due()) publishFrame(MQTT_TOPIC_IMAGE, true);
}

#endif  // STREAM_MODE == MODE_MQTT
