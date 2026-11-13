#pragma once
#include <Arduino.h>
#include <WiFi.h>

// WiFi con reconexion por backoff exponencial y SIN bloquear el loop:
// netLoop() vuelve enseguida siempre, incluso mientras reintenta.
void      netBegin();
void      netLoop();
bool      netIsUp();
IPAddress netIP();
