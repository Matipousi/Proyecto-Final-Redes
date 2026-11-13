#include "metrics.h"
#include <WiFi.h>
#include <stdarg.h>

Metrics g_m;

static const char* CSV_HEADER =
  "ts_ms,frame_n,bytes,capture_ms,send_ms,heap_free,rssi,dropped";

void logComment(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial.print("# ");
  Serial.println(buf);
}

void metricsBegin() {
  memset(&g_m, 0, sizeof(g_m));
  g_m.boot_ms      = millis();
  g_m.win_start_ms = g_m.boot_ms;
#if CSV_ENABLED
  Serial.println(CSV_HEADER);
#endif
}

void metricsFrameSent(size_t bytes, uint32_t capture_ms, uint32_t send_ms) {
  g_m.frame_n++;
  g_m.bytes_total += bytes;
  g_m.win_frames++;
  g_m.win_bytes += bytes;

#if CSV_ENABLED
  // Fila CSV. Sin printf de floats: mas barato y no depende del locale.
  Serial.printf("%lu,%lu,%u,%lu,%lu,%lu,%d,%lu\n",
                (unsigned long)millis(),
                (unsigned long)g_m.frame_n,
                (unsigned)bytes,
                (unsigned long)capture_ms,
                (unsigned long)send_ms,
                (unsigned long)ESP.getFreeHeap(),
                (int)WiFi.RSSI(),
                (unsigned long)g_m.dropped);
#endif
}

void metricsFrameDropped(DropReason reason, uint32_t count) {
  g_m.dropped += count;
  switch (reason) {
    case DROP_SCHEDULE:      g_m.drop_schedule     += count; break;
    case DROP_CAPTURE:       g_m.drop_capture      += count; break;
    case DROP_BACKPRESSURE:  g_m.drop_backpressure += count; break;
    case DROP_NO_CLIENT:     g_m.drop_no_client    += count; break;
    case DROP_TRANSPORT:     g_m.drop_transport    += count; break;
  }
}

bool metricsTick() {
  uint32_t now = millis();
  uint32_t elapsed = now - g_m.win_start_ms;
  if (elapsed < TELEMETRY_INTERVAL_MS) return false;

  g_m.fps           = (g_m.win_frames * 1000.0f) / (float)elapsed;
  g_m.bytes_per_sec = (uint32_t)((uint64_t)g_m.win_bytes * 1000ULL / elapsed);

  g_m.win_frames   = 0;
  g_m.win_bytes    = 0;
  g_m.win_start_ms = now;
  g_m.window_seq++;
  return true;
}

size_t metricsToJson(char* out, size_t out_len) {
  int fps_x10 = (int)(g_m.fps * 10.0f + 0.5f);
  return (size_t)snprintf(out, out_len,
    "{\"fps\":%d.%d,\"bytes_s\":%lu,\"rssi\":%d,\"heap_free\":%lu,"
    "\"dropped\":%lu,\"frames\":%lu,\"uptime_s\":%lu,"
    "\"drop_detail\":{\"sched\":%lu,\"cap\":%lu,\"bp\":%lu,\"noclient\":%lu,\"tx\":%lu}}",
    fps_x10 / 10, fps_x10 % 10,
    (unsigned long)g_m.bytes_per_sec,
    (int)WiFi.RSSI(),
    (unsigned long)ESP.getFreeHeap(),
    (unsigned long)g_m.dropped,
    (unsigned long)g_m.frame_n,
    (unsigned long)((millis() - g_m.boot_ms) / 1000UL),
    (unsigned long)g_m.drop_schedule,
    (unsigned long)g_m.drop_capture,
    (unsigned long)g_m.drop_backpressure,
    (unsigned long)g_m.drop_no_client,
    (unsigned long)g_m.drop_transport);
}
