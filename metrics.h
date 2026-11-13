#pragma once
#include <Arduino.h>
#include "config.h"

// =============================================================================
//  metrics.h  -  Struct de metricas UNICA y compartida por los tres modos.
//
//  Salida por Serial en CSV, una linea por frame ENVIADO:
//    ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped
//
//  Todo lo que NO es una fila CSV se emite con prefijo '#', para que el archivo
//  siga siendo un CSV valido y se pueda leer directo:
//    pandas.read_csv("captura.csv", comment="#")
// =============================================================================

// Por que se descarto un frame. Se contabiliza siempre, en los tres modos.
enum DropReason {
  DROP_SCHEDULE,   // se paso la ranura de tiempo: no llegamos al FPS objetivo
  DROP_CAPTURE,    // esp_camera_fb_get() devolvio NULL
  DROP_BACKPRESSURE, // el transporte no acepta mas datos (cola WS llena, socket trabado)
  DROP_NO_CLIENT,  // no hay a quien mandarle (solo informativo)
  DROP_TRANSPORT   // fallo el envio (publish rechazado, escritura corta)
};

struct Metrics {
  // Acumulados desde el boot
  uint32_t frame_n;        // frames enviados con exito
  uint32_t dropped;        // frames que debian salir y no salieron
  uint64_t bytes_total;

  // Desglose de descartes (util para explicar el "por que" en el informe)
  uint32_t drop_schedule;
  uint32_t drop_capture;
  uint32_t drop_backpressure;
  uint32_t drop_no_client;
  uint32_t drop_transport;

  // Ventana de agregacion de 1 s -> lo que se publica como telemetria
  uint32_t win_frames;
  uint32_t win_bytes;
  uint32_t win_start_ms;

  float    fps;            // ultimo valor calculado sobre la ventana
  uint32_t bytes_per_sec;  // idem
  uint32_t window_seq;     // se incrementa al cerrar cada ventana: permite a un
                           // modo publicar telemetria exactamente cuando hay
                           // datos nuevos, sin reiniciar la ventana por su lado

  uint32_t boot_ms;
};

extern Metrics g_m;

void metricsBegin();

// Registra un frame efectivamente puesto en la red. Imprime la fila CSV.
void metricsFrameSent(size_t bytes, uint32_t capture_ms, uint32_t send_ms);

// Registra un frame que no salio. No imprime fila (dropped es una COLUMNA
// acumulada, no un tipo de fila): la fila siguiente muestra el contador subido.
void metricsFrameDropped(DropReason reason, uint32_t count = 1);

// Cierra la ventana de 1 s y recalcula fps / bytes_per_sec. Llamar desde loop().
// Devuelve true en el tick en que la ventana se cerro.
bool metricsTick();

// Telemetria en JSON, formato compartido (MQTT la publica, los otros modos la
// exponen por HTTP en /status: mismos campos, misma unidad, comparables).
size_t metricsToJson(char* out, size_t out_len);

// Log fuera de banda: siempre con '#' adelante para no ensuciar el CSV.
void logComment(const char* fmt, ...);
