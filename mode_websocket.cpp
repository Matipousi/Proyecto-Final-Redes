#include "config.h"
#if STREAM_MODE == MODE_WEBSOCKET

// =============================================================================
//  MODO 2  -  WebSocket binario. El equipo es SERVIDOR.
//
//  Puerto 80: sirve la pagina cliente Y hace el upgrade a WebSocket en /stream.
//  Un JPEG completo por mensaje binario. La libreria puede dividir ese mensaje
//  en varios FRAMES WebSocket segun el espacio del buffer TCP; siguen formando
//  un unico mensaje y el navegador lo recibe como un solo Blob.
//
//  Lo que se busca ver en la captura:
//    - el handshake HTTP con "101 Switching Protocols" limpio, una sola vez
//    - de ahi en mas, mensajes WebSocket binarios sobre la misma conexion TCP
//    - los controles viajan al reves como mensajes de texto por ESE MISMO canal
//    - sin ping/pong automatico: los frames de control se distinguen claramente
//
//  A diferencia del modo 1, aca no hay cabeceras por frame donde meter el numero
//  de ranura: el mensaje es el JPEG y nada mas. La correlacion con el CSV se
//  hace por orden y por tamanio.
// =============================================================================
#include <ESPAsyncWebServer.h>
#include <WiFi.h>

#include "camera.h"
#include "fb_guard.h"
#include "frame_clock.h"
#include "metrics.h"
#include "modes.h"
#include "net.h"

static AsyncWebServer s_server(HTTP_UI_PORT);
static AsyncWebSocket s_ws(WS_PATH);
static FrameClock     s_clock;

static uint32_t s_client_id = 0;      // 0 = no hay nadie

// Configuracion interactiva exclusiva del modo WebSocket. Los pedidos llegan
// desde la tarea de red y se aplican en streamModeLoop(), nunca mientras se esta
// capturando o encolando un frame.
static volatile int      s_requested_framesize = -1;
static volatile uint16_t s_requested_fps       = 0;
static volatile bool     s_send_config         = false;
static framesize_t       s_current_framesize   = CAM_FRAMESIZE;
static uint16_t          s_target_fps          = TARGET_FPS;
static uint32_t          s_frame_interval_ms   = FRAME_INTERVAL_MS;

// Un frame encolado y todavia no drenado. Ver nota sobre send_ms mas abajo.
static bool     s_pending           = false;
static uint32_t s_pending_bytes     = 0;
static uint32_t s_pending_capture   = 0;
static uint32_t s_pending_t0        = 0;

// -----------------------------------------------------------------------------
// Pagina cliente
// -----------------------------------------------------------------------------
static const char INDEX_HTML[] PROGMEM = R"HTML(<!doctype html>
<meta charset="utf-8"><meta name=viewport content="width=device-width,initial-scale=1">
<title>camara_redes - WebSocket</title>
<style>body{font:14px system-ui;margin:0;background:#111;color:#eee}
header,.controls{padding:8px 12px;background:#1c1c1c}.controls{display:flex;gap:14px;align-items:center;border-top:1px solid #333;flex-wrap:wrap}
select,button{font:inherit;padding:4px 8px}.preview{width:min(800px,100%);aspect-ratio:4/3;background:#000}
.preview img{display:block;width:100%;height:100%;object-fit:fill}
pre{margin:8px 12px;color:#9c9}</style>
<header>MODO 2 &mdash; WebSocket binario &middot; ws://<span id=h></span>/stream</header>
<div class=controls>
<label>Resoluci&oacute;n <select id=r disabled><option value=QVGA>320&times;240</option><option value=VGA selected>640&times;480</option><option value=SVGA>800&times;600</option></select></label>
<label>FPS <select id=f disabled><option>1</option><option>2</option><option>5</option><option selected>10</option><option>15</option><option>20</option><option>25</option><option>30</option></select></label>
<button id=reset disabled>640&times;480 / 10 FPS</button><span id=c>esperando configuraci&oacute;n...</span>
</div>
<div class=preview><img id=v></div><pre id=s>conectando...</pre>
<script>
h.textContent=location.host;
let n=0,b=0,t=Date.now(),u=null;
const resolutionLabel={QVGA:'320\u00d7240',VGA:'640\u00d7480',SVGA:'800\u00d7600'};
const ws=new WebSocket('ws://'+location.host+'/stream');
ws.binaryType='blob';
function send(x){if(ws.readyState===WebSocket.OPEN)ws.send(x)}
r.onchange=()=>send('resolution:'+r.value);
f.onchange=()=>send('fps:'+f.value);
reset.onclick=()=>{send('resolution:VGA');send('fps:10')};
ws.onopen =()=>{s.textContent='conectado (101 Switching Protocols)';r.disabled=f.disabled=reset.disabled=false;send('config:get')};
ws.onclose=()=>{s.textContent='cerrado';r.disabled=f.disabled=reset.disabled=true};
ws.onmessage=e=>{
  if(typeof e.data==='string'){
    try{const m=JSON.parse(e.data);if(m.type==='config'){r.value=m.resolution;f.value=String(m.fps);c.textContent=`activo: ${resolutionLabel[m.resolution]||m.resolution} / ${m.fps} FPS`}else if(m.type==='error'){c.textContent='error: '+m.message}}
    catch(_){c.textContent=e.data}
    return;
  }
  n++; b+=e.data.size;
  if(u) URL.revokeObjectURL(u);      // sin esto el navegador se come la RAM
  u=URL.createObjectURL(e.data); v.src=u;
  const d=(Date.now()-t)/1000;
  if(d>=1){s.textContent=`${(n/d).toFixed(1)} fps  ${(b/d/1024).toFixed(0)} KiB/s`;n=0;b=0;t=Date.now()}
};
</script>)HTML";

static const char* frameSizeName(framesize_t fs) {
  switch (fs) {
    case FRAMESIZE_QVGA: return "QVGA";
    case FRAMESIZE_VGA:  return "VGA";
    case FRAMESIZE_SVGA: return "SVGA";
    default:             return "?";
  }
}

static int parseFrameSize(const char* name) {
  if (strcmp(name, "QVGA") == 0) return FRAMESIZE_QVGA;
  if (strcmp(name, "VGA")  == 0) return FRAMESIZE_VGA;
  if (strcmp(name, "SVGA") == 0) return FRAMESIZE_SVGA;
  return -1;
}

static void sendConfig(AsyncWebSocketClient* c) {
  char json[96];
  snprintf(json, sizeof(json),
           "{\"type\":\"config\",\"resolution\":\"%s\",\"fps\":%u}",
           frameSizeName(s_current_framesize), (unsigned)s_target_fps);
  c->text(json);
}

static void sendControlError(AsyncWebSocketClient* c, const char* message) {
  char json[128];
  snprintf(json, sizeof(json), "{\"type\":\"error\",\"message\":\"%s\"}", message);
  c->text(json);
}

// -----------------------------------------------------------------------------
// Eventos del WebSocket
// -----------------------------------------------------------------------------
static void onWsEvent(AsyncWebSocket* server, AsyncWebSocketClient* client,
                      AwsEventType type, void* arg, uint8_t* data, size_t len) {
  switch (type) {
    case WS_EVT_CONNECT:
      // Un solo espectador, igual que en el modo 1: con dos, el ancho de banda
      // se reparte y las metricas dejan de significar lo que dicen.
      if (s_client_id != 0 && server->client(s_client_id) != nullptr) {
        logComment("ws: rechazo a %s, ya hay un cliente",
                   client->remoteIP().toString().c_str());
        client->close(1013, "ya hay un cliente");   // 1013 = try again later
        return;
      }
      s_client_id = client->id();
      // Auto-ping desactivado (default): asi todo frame WS de la captura es
      // video, sin control frames intercalados.
      client->keepAlivePeriod(0);
      // Si la cola se llena NO cerramos al cliente: preferimos descartar el
      // frame y contarlo. Un corte de conexion ensuciaria la comparacion entre
      // modos. Es el default de la libreria; queda explicito a proposito.
      client->setCloseClientOnQueueFull(false);
      s_clock.resync();
      s_send_config = true;
      logComment("ws: cliente %u desde %s, handshake 101 completado",
                 (unsigned)client->id(), client->remoteIP().toString().c_str());
      break;

    case WS_EVT_DATA: {
      if (client->id() != s_client_id) break;
      AwsFrameInfo* info = static_cast<AwsFrameInfo*>(arg);
      // Los comandos de esta pagina siempre caben en un unico frame de texto.
      if (!info || info->opcode != WS_TEXT || !info->final || info->index != 0 || info->len != len) {
        sendControlError(client, "comando debe ser un mensaje de texto corto");
        break;
      }

      char cmd[40];
      size_t n = (len < sizeof(cmd) - 1) ? len : sizeof(cmd) - 1;
      memcpy(cmd, data, n);
      cmd[n] = 0;

      if (strcmp(cmd, "config:get") == 0) {
        s_send_config = true;
      } else if (strncmp(cmd, "fps:", 4) == 0) {
        long fps = strtol(cmd + 4, nullptr, 10);
        if (fps < 1 || fps > 30) sendControlError(client, "FPS debe estar entre 1 y 30");
        else s_requested_fps = (uint16_t)fps;
      } else if (strncmp(cmd, "resolution:", 11) == 0) {
        int fs = parseFrameSize(cmd + 11);
        if (fs < 0) sendControlError(client, "resolucion valida: QVGA, VGA o SVGA");
        else s_requested_framesize = fs;
      } else {
        sendControlError(client, "comando desconocido");
      }
      break;
    }

    case WS_EVT_DISCONNECT:
      if (client->id() == s_client_id) {
        s_client_id = 0;
        if (s_pending) { metricsFrameDropped(DROP_TRANSPORT); s_pending = false; }
        logComment("ws: cliente desconectado tras %lu frames enviados",
                   (unsigned long)g_m.frame_n);
      }
      break;

    case WS_EVT_ERROR:
      logComment("ws: error en cliente %u", (unsigned)client->id());
      break;

    default:
      break;
  }
}

// Aplica todos los pedidos juntos en un punto seguro y confirma el resultado
// por el mismo WebSocket. En Wireshark: texto cliente->servidor, respuesta de
// texto servidor->cliente y video binario servidor->cliente.
static void applyRequestedConfig(AsyncWebSocketClient* c) {
  if (s_pending || c->queueLen() > 0) return;

  int requested_fs = s_requested_framesize;
  uint16_t requested_fps = s_requested_fps;
  s_requested_framesize = -1;
  s_requested_fps = 0;

  if (requested_fs >= 0) {
    sensor_t* sensor = esp_camera_sensor_get();
    if (sensor && sensor->set_framesize(sensor, (framesize_t)requested_fs) == 0) {
      s_current_framesize = (framesize_t)requested_fs;
      s_clock.resync();
      logComment("ws: resolucion cambiada a %s por mensaje WebSocket", frameSizeName(s_current_framesize));
    } else {
      sendControlError(c, "la camara rechazo la resolucion");
    }
  }

  if (requested_fps != 0) {
    s_target_fps = requested_fps;
    s_frame_interval_ms = 1000UL / s_target_fps;
    if (s_frame_interval_ms == 0) s_frame_interval_ms = 1;
    s_clock.begin(s_frame_interval_ms);
    logComment("ws: FPS objetivo cambiado a %u por mensaje WebSocket", (unsigned)s_target_fps);
  }

  if (requested_fs >= 0 || requested_fps != 0) s_send_config = true;
  if (s_send_config && c->queueLen() == 0) {
    s_send_config = false;
    sendConfig(c);
  }
}

// -----------------------------------------------------------------------------
// Envio
// -----------------------------------------------------------------------------
static void sendFrame(AsyncWebSocketClient* c) {
  uint32_t t0 = millis();
  FbGuard  fb(esp_camera_fb_get());
  uint32_t capture_ms = millis() - t0;

  if (!fb) {
    metricsFrameDropped(DROP_CAPTURE);
    return;
  }

  // Guarda de memoria: binary() COPIA el frame a un buffer en heap interno
  // (no en PSRAM). Si el heap no da para el frame con margen, mejor descartarlo
  // que quedarse sin RAM en medio de la corrida.
  if (ESP.getFreeHeap() < fb->len * 2 + 32768) {
    metricsFrameDropped(DROP_BACKPRESSURE);
    return;
  }

  uint32_t t1 = millis();
  if (!c->binary(fb->buf, fb->len)) {
    metricsFrameDropped(DROP_TRANSPORT);
    return;
  }

  // El frame quedo ENCOLADO, no enviado: binary() vuelve enseguida. La fila del
  // CSV se emite recien cuando la cola se vacia (ver streamModeLoop), asi el
  // send_ms del modo 2 mide lo mismo que el del modo 1: tiempo hasta soltar el
  // frame a la red, no tiempo de memcpy.
  s_pending         = true;
  s_pending_bytes   = fb->len;
  s_pending_capture = capture_ms;
  s_pending_t0      = t1;
}

// -----------------------------------------------------------------------------
// Interfaz de modo
// -----------------------------------------------------------------------------
const char* streamModeName() { return "MODE_WEBSOCKET (servidor, :80 UI + WS " WS_PATH ")"; }

void streamModeSetup() {
  s_ws.onEvent(onWsEvent);
  s_server.addHandler(&s_ws);

  s_server.on("/", HTTP_GET, [](AsyncWebServerRequest* req) {
    req->send(200, "text/html", INDEX_HTML);
  });
  s_server.on("/status", HTTP_GET, [](AsyncWebServerRequest* req) {
    char json[384];
    metricsToJson(json, sizeof(json));
    req->send(200, "application/json", json);
  });
  s_server.onNotFound([](AsyncWebServerRequest* req) {
    req->send(404, "text/plain", "no existe");
  });

  s_server.begin();
  s_clock.begin(s_frame_interval_ms);

  logComment("ws: UI en http://%s/", netIP().toString().c_str());
  logComment("ws: stream en ws://%s%s", netIP().toString().c_str(), WS_PATH);
}

void streamModeLoop() {
  s_ws.cleanupClients();

  AsyncWebSocketClient* c = (s_client_id != 0) ? s_ws.client(s_client_id) : nullptr;
  if (c && c->status() != WS_CONNECTED) c = nullptr;

  if (!c) {
    s_client_id = 0;
    if (s_pending) { metricsFrameDropped(DROP_TRANSPORT); s_pending = false; }
    s_clock.resync();
    return;
  }

  // El frame anterior termino de drenar: recien ahora se registra, con el
  // send_ms real (encolado -> salido).
  if (s_pending && c->queueLen() == 0) {
    metricsFrameSent(s_pending_bytes, s_pending_capture, millis() - s_pending_t0);
    s_pending = false;
  }

  applyRequestedConfig(c);

  if (!s_clock.due()) return;

  // ESTA es la trampa del modo asincronico. canSend() de la libreria permite
  // hasta WS_MAX_QUEUED_MESSAGES (32) mensajes en cola; con frames VGA de ~30 KB
  // eso serian ~1 MB de heap interno y la placa se queda sin RAM.
  // Politica: como mucho UN frame en vuelo. Si el anterior no drenó, el nuevo se
  // descarta y se cuenta. En un stream en vivo el frame viejo no sirve igual.
  if (s_pending || c->queueLen() > 0) {
    metricsFrameDropped(DROP_BACKPRESSURE);
    return;
  }

  sendFrame(c);
}

#endif  // STREAM_MODE == MODE_WEBSOCKET
