#include "net.h"
#include "config.h"
#include "metrics.h"

static uint32_t s_next_retry_ms = 0;
static uint32_t s_backoff_ms    = WIFI_RETRY_BASE_MS;
static bool     s_was_up        = false;

static void startAttempt() {
  WiFi.disconnect(false, false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void netBegin() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(WIFI_HOSTNAME);

  // Reconexion manual con backoff: la del core reintenta a su propio ritmo y
  // ensucia la captura con intentos que no controlamos.
  WiFi.setAutoReconnect(false);

  // Modem sleep OFF. Con power save el AP bufferea tramas y las entrega a
  // rafagas: los tiempos entre paquetes en Wireshark dejarian de reflejar el
  // ritmo real del emisor. Cuesta bateria, compra observabilidad.
  WiFi.setSleep(false);

#if WIFI_USE_STATIC_IP
  IPAddress ip(WIFI_STATIC_IP), gw(WIFI_GATEWAY), mask(WIFI_SUBNET), dns(WIFI_DNS);
  if (!WiFi.config(ip, gw, mask, dns)) {
    logComment("aviso: WiFi.config() rechazo la IP fija, sigo con DHCP");
  }
#endif

  logComment("wifi: conectando a \"%s\" (802.11 b/g/n, solo 2.4 GHz)", WIFI_SSID);
  startAttempt();
  // El primer intento tiene su ventana completa antes de que se lo declare
  // fallido: reintentar antes solo lo interrumpe.
  s_next_retry_ms = millis() + WIFI_ATTEMPT_TIMEOUT_MS;
}

void netLoop() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!s_was_up) {
      s_was_up     = true;
      s_backoff_ms = WIFI_RETRY_BASE_MS;   // reset del backoff al reconectar
      logComment("wifi: OK  ip=%s  rssi=%d dBm  canal=%d  mac=%s",
                 WiFi.localIP().toString().c_str(), (int)WiFi.RSSI(),
                 (int)WiFi.channel(), WiFi.macAddress().c_str());
    }
    return;
  }

  if (s_was_up) {
    s_was_up        = false;
    s_next_retry_ms = millis();      // se cayo estando arriba: reconectar ya
    logComment("wifi: CAIDO, reintento con backoff");
  }

  uint32_t now = millis();
  if ((int32_t)(now - s_next_retry_ms) < 0) return;   // todavia no toca: no bloquea

  logComment("wifi: reintento (backoff %lu ms)", (unsigned long)s_backoff_ms);
  startAttempt();

  // La proxima evaluacion recien cuando ESTE intento haya tenido su ventana.
  s_next_retry_ms = now + WIFI_ATTEMPT_TIMEOUT_MS + s_backoff_ms;
  s_backoff_ms = (s_backoff_ms * 2 > WIFI_RETRY_MAX_MS) ? WIFI_RETRY_MAX_MS
                                                        : s_backoff_ms * 2;
}

bool      netIsUp() { return WiFi.status() == WL_CONNECTED; }
IPAddress netIP()   { return WiFi.localIP(); }
