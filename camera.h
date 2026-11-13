#pragma once
#include "esp_camera.h"

// Inicializa la OV2640 con la config del experimento.
// Si no encuentra PSRAM se detiene a proposito: una corrida degradada en
// silencio (menos buffers, menor resolucion) invalidaria la comparacion entre
// modos sin que se note en la captura.
bool cameraBegin();

// Describe la config efectiva por Serial, como comentarios '#'.
void cameraLogConfig();
