#include "camera.h"
#include "camera_pins.h"
#include "config.h"
#include "metrics.h"
#include <Arduino.h>

bool cameraBegin() {
  // La PSRAM es un requisito, no una optimizacion: con fb_count=2 en VGA hacen
  // falta cientos de KB que no entran en la SRAM interna.
  if (!psramFound()) {
    logComment("ERROR: no se detecta PSRAM.");
    logComment("ERROR: Arduino IDE -> Herramientas -> PSRAM: \"OPI PSRAM\"");
    logComment("ERROR: sin PSRAM la corrida no es comparable. Me detengo aca.");
    return false;
  }

  camera_config_t cfg;
  memset(&cfg, 0, sizeof(cfg));

  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.ledc_timer   = LEDC_TIMER_0;

  cfg.pin_d0 = Y2_GPIO_NUM;   // el driver numera D0..D7; el pinout, Y2..Y9
  cfg.pin_d1 = Y3_GPIO_NUM;
  cfg.pin_d2 = Y4_GPIO_NUM;
  cfg.pin_d3 = Y5_GPIO_NUM;
  cfg.pin_d4 = Y6_GPIO_NUM;
  cfg.pin_d5 = Y7_GPIO_NUM;
  cfg.pin_d6 = Y8_GPIO_NUM;
  cfg.pin_d7 = Y9_GPIO_NUM;

  cfg.pin_xclk     = XCLK_GPIO_NUM;
  cfg.pin_pclk     = PCLK_GPIO_NUM;
  cfg.pin_vsync    = VSYNC_GPIO_NUM;
  cfg.pin_href     = HREF_GPIO_NUM;
  cfg.pin_sccb_sda = SIOD_GPIO_NUM;
  cfg.pin_sccb_scl = SIOC_GPIO_NUM;
  cfg.pin_pwdn     = PWDN_GPIO_NUM;
  cfg.pin_reset    = RESET_GPIO_NUM;

  cfg.xclk_freq_hz = CAM_XCLK_HZ;
  cfg.pixel_format = PIXFORMAT_JPEG;   // la OV2640 comprime en el sensor
  cfg.frame_size   = CAM_FRAMESIZE;
  cfg.jpeg_quality = CAM_JPEG_QUALITY;
  cfg.fb_count     = CAM_FB_COUNT;

  cfg.fb_location  = CAMERA_FB_IN_PSRAM;
  // GRAB_LATEST: al pedir un frame se entrega el mas nuevo y se descartan los
  // viejos. Con WHEN_EMPTY la cola introduce latencia creciente y la captura
  // mostraria un video "atrasado" respecto de los timestamps del CSV.
  cfg.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) {
    logComment("ERROR: esp_camera_init fallo: 0x%x", err);
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    // El sensor del modulo Sense viene espejado verticalmente.
    s->set_vflip(s, 1);
    s->set_hmirror(s, 0);
  }

  // Primer frame de descarte: el sensor entrega basura hasta que estabiliza el
  // AGC/AWB, y no queremos ese frame contaminando la primera fila del CSV.
  camera_fb_t* warmup = esp_camera_fb_get();
  if (warmup) esp_camera_fb_return(warmup);

  return true;
}

void cameraLogConfig() {
  const char* fs = "?";
  switch ((int)CAM_FRAMESIZE) {
    case FRAMESIZE_QVGA: fs = "QVGA 320x240";   break;
    case FRAMESIZE_VGA:  fs = "VGA 640x480";    break;
    case FRAMESIZE_SVGA: fs = "SVGA 800x600";   break;
    case FRAMESIZE_XGA:  fs = "XGA 1024x768";   break;
    case FRAMESIZE_HD:   fs = "HD 1280x720";    break;
    case FRAMESIZE_SXGA: fs = "SXGA 1280x1024"; break;
    case FRAMESIZE_UXGA: fs = "UXGA 1600x1200"; break;
    default: break;
  }
  logComment("camara: %s, calidad JPEG %d, fb_count %d, PSRAM, GRAB_LATEST",
             fs, (int)CAM_JPEG_QUALITY, (int)CAM_FB_COUNT);
  logComment("psram total %u B, libre %u B",
             (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram());
}
