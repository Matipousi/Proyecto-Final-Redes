#pragma once
// Interfaz unica de los tres modos. Cada mode_*.cpp define estas dos funciones
// dentro de su propio #if STREAM_MODE == ..., asi que en cada compilacion existe
// exactamente una implementacion y los otros dos archivos quedan vacios (no
// arrastran includes ni exigen tener instaladas sus librerias).
void streamModeSetup();
void streamModeLoop();

// Nombre del modo activo, para el banner y el CSV.
const char* streamModeName();
