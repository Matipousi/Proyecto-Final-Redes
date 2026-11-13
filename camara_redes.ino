// =============================================================================
//  camara_redes  -  Maqueta de medicion para Redes de Datos
//  Seeed Studio XIAO ESP32S3 Sense (ESP32-S3R8) + OV2640
//
//  El mismo stream de video sobre tres arquitecturas de transporte:
//    1) MODE_HTTP_MJPEG  el equipo es SERVIDOR : HTTP multipart en el puerto 81
//    2) MODE_WEBSOCKET   el equipo es SERVIDOR : WebSocket binario en el 80
//    3) MODE_MQTT        el equipo es CLIENTE  : sale hacia el broker, 1883
//
//  Se elige el modo en config.h (#define STREAM_MODE). La instrumentacion es
//  identica en los tres: eso es lo que hace comparables las capturas.
//
//  Sin TLS y sin autenticacion a proposito en esta etapa: la captura tiene que
//  poder leerse en claro. Ganchos preparados al final de config.h.
// =============================================================================
#include "config.h"
#include "camera.h"
#include "metrics.h"
#include "modes.h"
#include "net.h"

#include <esp_log.h>

static bool s_camera_ok = false;

void setup() {
  Serial.begin(SERIAL_BAUD);
  // Antes que nada: que escribir por Serial NO pueda frenar el loop. Ver la nota
  // de SERIAL_TX_TIMEOUT_MS en config.h; sin esto el propio CSV estrangula el
  // stream cuando nadie tiene el monitor abierto.
  Serial.setTxTimeoutMs(SERIAL_TX_TIMEOUT_MS);
  delay(300);                 // que el puerto USB-CDC enumere antes del banner

#if SUPPRESS_IDF_LOGS
  // Los logs de ESP-IDF no llevan '#' y romperian el CSV. Todo lo que hace
  // falta para diagnosticar ya sale por logComment().
  esp_log_level_set("*", ESP_LOG_NONE);
#endif

  Serial.println();
  logComment("=== camara_redes | modo: %s ===", streamModeName());
  logComment("target_fps=%d  intervalo=%d ms", (int)TARGET_FPS, (int)FRAME_INTERVAL_MS);
  logComment("TLS: deshabilitado a proposito (captura en texto plano)");

  s_camera_ok = cameraBegin();
  if (!s_camera_ok) return;   // cameraBegin() ya explico el motivo
  cameraLogConfig();

  netBegin();
  while (!netIsUp()) {        // el arranque si espera: sin IP no hay nada que medir
    netLoop();
    delay(50);
  }
  netLoop();                  // emite la linea "wifi: OK ip=..." ANTES de seguir:
                              // si no, saldria despues del encabezado del CSV

  streamModeSetup();

  metricsBegin();             // imprime el encabezado CSV: de aca en mas, datos
}

void loop() {
  if (!s_camera_ok) {         // fallo duro: avisar y no fingir que se esta midiendo
    logComment("detenido: la camara no inicializo");
    delay(5000);
    return;
  }

  netLoop();                  // reconexion WiFi con backoff, no bloqueante
  streamModeLoop();           // captura + envio a FPS objetivo
  metricsTick();              // cierra la ventana de 1 s (fps, bytes/s)

  // Cede CPU al scheduler: alimenta el watchdog y le da lugar al stack TCP/IP,
  // que corre en otra tarea. Sin esto el loop de streaming lo deja sin aire.
  delay(1);
}
