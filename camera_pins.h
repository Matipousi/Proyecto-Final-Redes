#pragma once
// =============================================================================
//  camera_pins.h  -  Pinout del Seeed XIAO ESP32S3 Sense.
//
//  Copia literal del bloque CAMERA_MODEL_XIAO_ESP32S3 de arduino-esp32:
//    packages/esp32/hardware/esp32/<ver>/libraries/ESP32/examples/
//        Camera/CameraWebServer/camera_pins.h
//
//  Va copiado porque ese archivo vive en la carpeta de EJEMPLOS del core: no
//  esta en el include path de un sketch propio. El define se mantiene igual al
//  oficial a proposito. NO usar un pinout generico de ESP32-CAM/AI-Thinker: el
//  XIAO tiene el suyo y con el equivocado la camara ni siquiera inicializa.
// =============================================================================

#define CAMERA_MODEL_XIAO_ESP32S3

#if defined(CAMERA_MODEL_XIAO_ESP32S3)
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39

#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13
#else
#error "Este sketch es para el XIAO ESP32S3 Sense"
#endif
