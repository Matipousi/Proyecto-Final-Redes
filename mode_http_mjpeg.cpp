#include "config.h"
#if STREAM_MODE == MODE_HTTP_MJPEG

// =============================================================================
//  MODO 1  -  HTTP MJPEG. El equipo es SERVIDOR.
//
//  Puerto 80 : pagina de control (WebServer del core).
//  Puerto 81 : el stream, sobre un WiFiServer crudo.
//
//  Los dos puertos estan separados a proposito: en la captura se ve al navegador
//  abrir una conexion corta al 80 para bajar el HTML y otra al 81 que ya no
//  cierra. Es la firma del modo, y con un solo puerto se confundiria.
//
//  El stream es un unico cuerpo HTTP infinito:
//    Content-Type: multipart/x-mixed-replace; boundary=frameboundary
//  y dentro, un JPEG por parte. La respuesta nunca termina, y por eso la
//  conexion TCP queda abierta: no hay un request por frame.
// =============================================================================
#include <WebServer.h>
#include <WiFi.h>

#include "camera.h"
#include "fb_guard.h"
#include "frame_clock.h"
#include "metrics.h"
#include "modes.h"
#include "net.h"

#define PART_BOUNDARY "frameboundary"

static WebServer  s_ui(HTTP_UI_PORT);
static WiFiServer s_stream_srv(HTTP_STREAM_PORT);
static WiFiClient s_stream_cli;
static FrameClock s_clock;

// Flag explicito de "hay stream abierto". NO se puede usar `if (s_stream_cli)`:
// en el core 3.x WiFiClient es NetworkClient y su operator bool() ES
// connected(), asi que al caerse el cliente el objeto ya da false y nunca se
// detectaria la transicion ni se cerraria el socket.
static bool s_stream_active = false;

// -----------------------------------------------------------------------------
// Pagina de control (puerto 80)
// -----------------------------------------------------------------------------
// El poll de /status arranca APAGADO: si estuviera prendido, cada segundo
// agregaria un GET al puerto 80 y ensuciaria la captura de referencia.
static const char INDEX_HTML[] PROGMEM = R"HTML(<!doctype html>
<meta charset="utf-8"><meta name=viewport content="width=device-width,initial-scale=1">
<title>camara_redes - MJPEG</title>
<style>body{font:14px system-ui;margin:0;background:#111;color:#eee}
header{padding:8px 12px;background:#1c1c1c}img{display:block;max-width:100%}
button{font:inherit;padding:4px 10px}pre{margin:8px 12px;color:#9c9}</style>
<header>MODO 1 &mdash; HTTP MJPEG &middot; stream en :81/stream
<button onclick="t()">medir /status</button></header>
<img id=v>
<pre id=s>poll apagado (no ensucia la captura)</pre>
<script>
v.src='http://'+location.hostname+':81/stream';
let h=null;
function t(){if(h){clearInterval(h);h=null;s.textContent='poll apagado';return}
h=setInterval(async()=>{s.textContent=await(await fetch('/status')).text()},1000)}
</script>)HTML";

static void handleIndex() {
  s_ui.send_P(200, "text/html", INDEX_HTML);
}

static void handleStatus() {
  char json[384];
  metricsToJson(json, sizeof(json));
  s_ui.send(200, "application/json", json);
}

// -----------------------------------------------------------------------------
// Puerto 81: aceptar al cliente del stream  -  SIN BLOQUEAR NUNCA
// -----------------------------------------------------------------------------
// Antes esto esperaba la request line con un read bloqueante. Con UN cliente no
// se notaba, pero cuando hay dos peleandose el stream (una pestania vieja que
// reintenta, un reload que dejo la conexion colgada) cada reintento robaba
// cientos de ms al loop, se perdian ranuras, y el desglose las cargaba a 'sched'
// sin que se entendiera por que.
//
// Ahora la request se lee de a pedazos a lo largo de varias vueltas del loop:
// se lee solo lo que ya esta disponible y se vuelve enseguida. HTTP_ACCEPT_
// TIMEOUT_MS pasa a ser un presupuesto de reloj de pared, no una espera.
static WiFiClient s_pend_cli;             // cliente aceptado, request a medio leer
static bool       s_pend_active = false;
static uint32_t   s_pend_since  = 0;
static char       s_pend_buf[192];
static size_t     s_pend_len    = 0;

static void sendStreamHeaders(WiFiClient& c) {
  c.print(
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: multipart/x-mixed-replace; boundary=" PART_BOUNDARY "\r\n"
    "Cache-Control: no-cache, no-store, must-revalidate\r\n"
    "Pragma: no-cache\r\n"
    "Access-Control-Allow-Origin: *\r\n"
    "Connection: close\r\n"
    "\r\n");
}

// Consume lo que YA llego, sin esperar. true cuando la request esta completa.
static bool pendingRequestReady() {
  while (s_pend_cli.available() && s_pend_len < sizeof(s_pend_buf) - 1) {
    s_pend_buf[s_pend_len++] = (char)s_pend_cli.read();
    s_pend_buf[s_pend_len]   = 0;
    if (s_pend_len >= 4 && strcmp(s_pend_buf + s_pend_len - 4, "\r\n\r\n") == 0) return true;
  }
  // Si se lleno el buffer alcanza igual: lo unico que se mira es la primera linea.
  return s_pend_len >= sizeof(s_pend_buf) - 1;
}

static void dropPending() {
  s_pend_cli.stop();
  s_pend_active = false;
  s_pend_len    = 0;
}

// Resuelve al cliente pendiente si su request ya llego. Devuelve true si la
// ranura de pendiente quedo libre.
static bool progressPending() {
  if (!s_pend_active) return true;

  if (!s_pend_cli.connected()) { dropPending(); return true; }

  if (!pendingRequestReady()) {
    // Presupuesto agotado sin que el cliente hablara (escaneo, preconnect).
    if ((uint32_t)(millis() - s_pend_since) > (uint32_t)HTTP_ACCEPT_TIMEOUT_MS) {
      dropPending();
      return true;
    }
    return false;                       // sigue pendiente, pero NO bloqueamos
  }

  bool quiere_stream = (strncmp(s_pend_buf, "GET /stream", 11) == 0) ||
                       (strncmp(s_pend_buf, "GET / ", 6) == 0);
  if (!quiere_stream) {
    s_pend_cli.print("HTTP/1.1 404 Not Found\r\n"
                     "Content-Type: text/plain\r\n"
                     "Connection: close\r\n\r\n"
                     "usar GET /stream\r\n");
    dropPending();
    return true;
  }

  // Llego tarde: mientras leiamos su request, otro se quedo con el stream.
  if (s_stream_active && s_stream_cli.connected()) {
    String quien = s_pend_cli.remoteIP().toString();
    s_pend_cli.print("HTTP/1.1 503 Service Unavailable\r\n"
                     "Content-Type: text/plain\r\n"
                     "Connection: close\r\n\r\n"
                     "ya hay un cliente conectado al stream\r\n");
    dropPending();
    logComment("http: 503 a %s (stream ya ocupado)", quien.c_str());
    return true;
  }

  // Nagle OFF: queremos que cada write() salga al cable cuando lo pedimos. Con
  // Nagle activo el stack junta escrituras y la temporizacion de los paquetes
  // dejaria de reflejar el ritmo del emisor.
  s_pend_cli.setNoDelay(true);
  // Un cliente que deja de leer llena la ventana TCP y write() se queda
  // esperando. Con esto el bloqueo queda acotado.
  s_pend_cli.setConnectionTimeout(HTTP_SEND_TIMEOUT_MS);

  s_stream_cli    = s_pend_cli;
  s_stream_active = true;
  s_pend_active   = false;
  s_pend_len      = 0;

  sendStreamHeaders(s_stream_cli);
  s_clock.resync();          // el reloj arranca recien cuando hay a quien enviarle

  logComment("http: cliente de stream %s conectado, multipart abierto",
             s_stream_cli.remoteIP().toString().c_str());
  return true;
}

static void acceptStreamClient() {
  // Fase A primero: si el pendiente ya mando su request, se resuelve AHORA y la
  // ranura queda libre en esta misma vuelta. Al reves (aceptar primero), un
  // cliente que reintenta en loop monopoliza la ranura y deja afuera al resto.
  if (!progressPending()) return;

  // Fase B: aceptar uno nuevo. Nunca se lee nada aca.
  WiFiClient nc = s_stream_srv.accept();
  if (!nc) return;

  // Un solo espectador por vez. Con dos, el ancho de banda se reparte y las
  // metricas dejan de significar lo que dicen: mejor rechazar explicito y que se
  // vea el 503 en la captura, antes que degradar la medicion en silencio.
  if (s_stream_active && s_stream_cli.connected()) {
    String quien = nc.remoteIP().toString();   // antes del stop(): despues da 0.0.0.0
    nc.print("HTTP/1.1 503 Service Unavailable\r\n"
             "Content-Type: text/plain\r\n"
             "Connection: close\r\n\r\n"
             "ya hay un cliente conectado al stream\r\n");
    nc.stop();
    logComment("http: 503 a %s (stream ya ocupado)", quien.c_str());
    return;
  }

  s_pend_cli    = nc;
  s_pend_active = true;
  s_pend_since  = millis();
  s_pend_len    = 0;
  s_pend_buf[0] = 0;
}

// -----------------------------------------------------------------------------
// Envio de un frame como una parte del multipart
// -----------------------------------------------------------------------------
static void sendFrame() {
  uint32_t t0 = millis();
  FbGuard  fb(esp_camera_fb_get());      // se devuelve solo al salir del scope
  uint32_t capture_ms = millis() - t0;

  if (!fb) {
    metricsFrameDropped(DROP_CAPTURE);
    return;                              // el buffer se devuelve igual (RAII)
  }

  // Cabecera de la parte. X-Slot / X-Frame-N / X-Timestamp-Ms no son estandar:
  // van para poder cruzar cada parte de la captura con su fila del CSV.
  //   X-Slot    numero de ranura del reloj de FPS -> un SALTO aca es un frame
  //             descartado, visible en Wireshark sin abrir el CSV.
  //   X-Frame-N numero de frame efectivamente enviado (nunca deja hueco).
  char head[224];
  int  hn = snprintf(head, sizeof(head),
                     "\r\n--" PART_BOUNDARY "\r\n"
                     "Content-Type: image/jpeg\r\n"
                     "Content-Length: %u\r\n"
                     "X-Slot: %lu\r\n"
                     "X-Frame-N: %lu\r\n"
                     "X-Timestamp-Ms: %lu\r\n"
                     "\r\n",
                     (unsigned)fb->len,
                     (unsigned long)s_clock.slot(),
                     (unsigned long)(g_m.frame_n + 1),
                     (unsigned long)millis());

  uint32_t t1 = millis();
  size_t   w1 = s_stream_cli.write((const uint8_t*)head, (size_t)hn);
  size_t   w2 = (w1 == (size_t)hn) ? s_stream_cli.write(fb->buf, fb->len) : 0;
  uint32_t send_ms = millis() - t1;

  if (w1 != (size_t)hn || w2 != fb->len) {
    // Escritura corta: el socket se lleno o el cliente se fue a mitad de camino.
    // La parte quedo truncada, asi que la conexion ya no es recuperable.
    metricsFrameDropped(DROP_BACKPRESSURE);
    logComment("http: escritura corta (%u/%u), cierro el stream",
               (unsigned)w2, (unsigned)fb->len);
    s_stream_cli.stop();
    s_stream_active = false;
    return;
  }

  metricsFrameSent(fb->len, capture_ms, send_ms);

  // Atribucion de la causa. Si la escritura tardo MAS que una ranura, no es que
  // el planificador llegara tarde: es que el consumidor no drena y TCP nos frena
  // a nosotros. Sin esto, esas ranuras caerian en 'sched' y el desglose por
  // causa diria cualquier cosa (era el caso: 2218 'sched' que en realidad eran
  // contrapresion).
  //
  // Que MJPEG sobre HTTP acople el ritmo del emisor al del receptor NO es un
  // bug: es la propiedad del transporte que este trabajo justamente compara.
  // Por eso se mide y se reporta, no se disimula.
  if (send_ms > (uint32_t)FRAME_INTERVAL_MS) {
    uint32_t perdidas = (send_ms / (uint32_t)FRAME_INTERVAL_MS) - 1;
    if (perdidas > 0) {
      metricsFrameDropped(DROP_BACKPRESSURE, perdidas);
      s_clock.resync();   // que due() no las cuente de nuevo como 'sched'
    }
    if (send_ms >= (uint32_t)HTTP_SLOW_CLIENT_WARN_MS) {
      logComment("http: cliente lento, envio de %lu ms (%lu ranuras perdidas)",
                 (unsigned long)send_ms, (unsigned long)perdidas);
    }
  }
}

// -----------------------------------------------------------------------------
// Interfaz de modo
// -----------------------------------------------------------------------------
const char* streamModeName() { return "MODE_HTTP_MJPEG (servidor, :80 UI + :81 stream)"; }

void streamModeSetup() {
  s_ui.on("/", handleIndex);
  s_ui.on("/status", handleStatus);
  s_ui.onNotFound([]() { s_ui.send(404, "text/plain", "no existe\r\n"); });
  s_ui.begin();

  s_stream_srv.begin();
  s_stream_srv.setNoDelay(true);

  s_clock.begin(FRAME_INTERVAL_MS);

  logComment("http: UI en http://%s/", netIP().toString().c_str());
  logComment("http: stream en http://%s:%d/stream", netIP().toString().c_str(), (int)HTTP_STREAM_PORT);
}

void streamModeLoop() {
#if DEBUG_LOOP_TIMING
  static uint32_t mx_ui = 0, mx_acc = 0, mx_snd = 0, mx_loop = 0, vueltas = 0, ventana = 0;
  static uint32_t t_prev = 0;
  uint32_t t_a = millis();
  if (t_prev && (t_a - t_prev) > mx_loop) mx_loop = t_a - t_prev;   // vuelta completa
  t_prev = t_a;
  vueltas++;
#endif

  s_ui.handleClient();       // puerto 80

#if DEBUG_LOOP_TIMING
  uint32_t t_b = millis();
  if (t_b - t_a > mx_ui) mx_ui = t_b - t_a;
#endif

  acceptStreamClient();      // puerto 81

#if DEBUG_LOOP_TIMING
  uint32_t t_c = millis();
  if (t_c - t_b > mx_acc) mx_acc = t_c - t_b;
  if (g_m.window_seq != ventana) {
    ventana = g_m.window_seq;
    logComment("loop: vueltas/s=%lu max_ui=%lu max_acc=%lu max_send=%lu max_vuelta=%lu | pend=%d len=%u stream=%d srv=%d",
               (unsigned long)vueltas, (unsigned long)mx_ui, (unsigned long)mx_acc,
               (unsigned long)mx_snd, (unsigned long)mx_loop,
               (int)s_pend_active, (unsigned)s_pend_len,
               (int)s_stream_active, (int)(bool)s_stream_srv);
    mx_ui = mx_acc = mx_snd = mx_loop = vueltas = 0;
  }
#endif

  if (s_stream_active && !s_stream_cli.connected()) {
    logComment("http: cliente de stream desconectado tras %lu frames enviados",
               (unsigned long)g_m.frame_n);
    s_stream_cli.stop();     // libera el descriptor; sin esto se filtran sockets
    s_stream_active = false;
  }

  if (!s_stream_active) {
    s_clock.resync();        // sin cliente no se acumulan descartes por ranura
    return;
  }

  if (s_clock.due()) {
#if DEBUG_LOOP_TIMING
    uint32_t t_s = millis();
    sendFrame();
    uint32_t d = millis() - t_s;
    if (d > mx_snd) mx_snd = d;
#else
    sendFrame();
#endif
  }
}

#endif  // STREAM_MODE == MODE_HTTP_MJPEG
