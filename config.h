#pragma once
// =============================================================================
//  config.h  -  Toda la configuracion del experimento en un solo lugar.
//
//  Maqueta de medicion para Redes de Datos: el mismo stream de video sobre tres
//  arquitecturas de transporte distintas. El firmware es el instrumento, no el
//  entregable: ante la duda entre rendimiento y observabilidad, gana
//  observabilidad.
// =============================================================================

// -----------------------------------------------------------------------------
// 1. SELECTOR DE MODO (tiempo de compilacion)
// -----------------------------------------------------------------------------
#define MODE_HTTP_MJPEG 1   // el equipo es SERVIDOR   : HTTP multipart, puerto 81
#define MODE_WEBSOCKET  2   // el equipo es SERVIDOR   : WebSocket binario, puerto 80
#define MODE_MQTT       3   // el equipo es CLIENTE    : sale hacia el broker, 1883

// >>> CAMBIAR ESTA LINEA PARA ELEGIR EL MODO A COMPILAR <<<
// El #ifndef es para poder forzar el modo desde afuera sin tocar el archivo
// (arduino-cli --build-property ...=-DSTREAM_MODE=2), util para compilar los
// tres de una. En el IDE no hay nada definido, asi que manda esta linea.
#ifndef STREAM_MODE
#define STREAM_MODE MODE_HTTP_MJPEG
#endif

// -----------------------------------------------------------------------------
// 2. RED
// -----------------------------------------------------------------------------
// Las credenciales NO van en este archivo, que se sube a git: van en
// config_local.h (en .gitignore). Ver config_local.example.h.
#if __has_include("config_local.h")
#include "config_local.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID       "CAMBIAME"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS       "CAMBIAME"
#endif
#define WIFI_HOSTNAME   "xiao-cam"

// IP fija opcional: deja la captura reproducible entre corridas (misma IP en los
// tres modos => los filtros de Wireshark no cambian). 0 = DHCP.
#define WIFI_USE_STATIC_IP 0
#define WIFI_STATIC_IP     192, 168, 1, 50
#define WIFI_GATEWAY       192, 168, 1, 1
#define WIFI_SUBNET        255, 255, 255, 0
#define WIFI_DNS           192, 168, 1, 1

// Backoff de reconexion WiFi (no bloqueante).
// OJO: asociarse + DHCP tarda tipicamente 2-6 s. Si se reintenta antes de que
// el intento en curso termine, el driver rechaza el nuevo WiFi.begin() con
// "sta is connecting, cannot set config" y encima ensucia el CSV. Por eso cada
// intento tiene su propia ventana, y el backoff se suma DESPUES de esa ventana.
#define WIFI_ATTEMPT_TIMEOUT_MS 8000
#define WIFI_RETRY_BASE_MS      1000
#define WIFI_RETRY_MAX_MS       30000

// -----------------------------------------------------------------------------
// 3. BROKER MQTT  (solo MODE_MQTT)
// -----------------------------------------------------------------------------
#define MQTT_BROKER_IP  "192.168.4.58"   // IP del Mosquitto en la LAN
#define MQTT_PORT       1883              // texto plano: es el punto del trabajo
#define MQTT_CLIENT_ID  "xiao-cam"

#define MQTT_TOPIC_TELEMETRY "camara/telemetria"
#define MQTT_TOPIC_CMD       "camara/cmd"
#define MQTT_TOPIC_IMAGE     "camara/imagen"

#define MQTT_QOS            0             // QoS 0: 1 PUBLISH, sin PUBACK. Ver README.

// PubSubClient arranca con un buffer de 256 B y DESCARTA EN SILENCIO todo lo que
// no entre: ni el JSON de telemetria completo entra ahi. Este valor cubre
// telemetria y cabeceras con margen; la imagen NO usa este buffer, se publica en
// streaming con beginPublish()/write()/endPublish().
#define MQTT_BUFFER_BYTES   2048

// Tope duro del payload de un PUBLISH con PubSubClient. No es el buffer: es que
// buildHeader() calcula la longitud en uint16_t. Pasado esto el paquete sale
// corrupto y sin error. Se deja margen para el topico y la cabecera.
#define MQTT_MAX_PAYLOAD_BYTES 65000
#define MQTT_RETRY_BASE_MS  1000
#define MQTT_RETRY_MAX_MS   30000

// Publicacion continua de frames en MODE_MQTT.
//   0 = comportamiento por defecto: solo telemetria 1 Hz + snapshot bajo demanda.
//   1 = ademas publica frames a TARGET_FPS, para comparar los tres modos con la
//       misma carga de video (mismo framesize, misma calidad, mismo FPS).
// Tambien se puede activar en caliente publicando "stream:on" en camara/cmd.
#define MQTT_STREAM_ENABLED 0

// -----------------------------------------------------------------------------
// 4. CAMARA  (OV2640 sobre la placa de expansion Sense)
// -----------------------------------------------------------------------------
// framesize: FRAMESIZE_QVGA(320x240) FRAMESIZE_VGA(640x480) FRAMESIZE_SVGA(800x600)
//            FRAMESIZE_HD(1280x720)  FRAMESIZE_UXGA(1600x1200)
// Con VGA @ 10 fps el bitrate cae comodo dentro del techo real de 802.11n HT20 1x1.
#define CAM_FRAMESIZE     FRAMESIZE_VGA
#define CAM_JPEG_QUALITY  12    // 10..63, MENOR = mejor calidad y mas bytes
#define CAM_FB_COUNT      2     // doble buffer, en PSRAM
#define CAM_XCLK_HZ       20000000

// -----------------------------------------------------------------------------
// 5. RITMO DEL STREAM
// -----------------------------------------------------------------------------
// FPS objetivo FIJO, no "lo mas rapido posible": es lo que hace comparables las
// tres capturas entre si. Un frame que no sale en su ranura cuenta como dropped.
#define TARGET_FPS        10
#define FRAME_INTERVAL_MS (1000 / (TARGET_FPS))

// -----------------------------------------------------------------------------
// 6. PUERTOS
// -----------------------------------------------------------------------------
// Cuanto puede bloquear una escritura al cliente del stream antes de darla por
// corta y cerrar. MJPEG sobre HTTP acopla el ritmo del emisor al del receptor
// via control de flujo de TCP: si el consumidor no drena, esto es lo que frena.
#define HTTP_SEND_TIMEOUT_MS   2000
// A partir de cuanto se avisa por Serial que el cliente esta lento.
#define HTTP_SLOW_CLIENT_WARN_MS 500

// Presupuesto para que un cliente nuevo del :81 mande su request. NO es una
// espera bloqueante: la request se lee de a pedazos a lo largo de varias vueltas
// del loop, asi que este valor solo decide cuando se abandona a un cliente que
// conecto y no hablo (un escaneo, un preconnect del navegador).
#define HTTP_ACCEPT_TIMEOUT_MS 1000

#define HTTP_UI_PORT      80    // pagina de control (modos 1 y 2)
#define HTTP_STREAM_PORT  81    // stream MJPEG (modo 1), separado a proposito
#define WS_PATH           "/stream"

// -----------------------------------------------------------------------------
// 7. INSTRUMENTACION
// -----------------------------------------------------------------------------
#define SERIAL_BAUD           115200

// LA trampa mas cara de todo el proyecto: el USB CDC nativo del S3 BLOQUEA al
// escribir si nadie esta drenando el puerto. El default del core es esperar
// hasta 20 x 100 ms = 2 SEGUNDOS por write(). Con una fila de CSV por frame,
// eso significa que abrir el monitor serie cambia el resultado de la medicion:
// con el monitor abierto daba 9,7 fps y sin el 0,4 fps, con la misma red.
//
// 0 = nunca bloquea; si el buffer esta lleno, la fila de CSV se PIERDE. Es lo
//     correcto para un instrumento: la instrumentacion jamas debe frenar a lo
//     que mide. Si nadie lee el puerto, no importa perder las filas.
// >0 = espera hasta esos ms. Usar solo si se necesita cada fila Y se esta
//     leyendo el puerto de verdad.
#define SERIAL_TX_TIMEOUT_MS  0

// Los logs internos de ESP-IDF (wifi, camara) salen SIN el prefijo '#' y
// romperian el parseo del CSV (pandas con comment="#" no los filtra).
// Poner en 0 mientras se depura para volver a verlos.
#define SUPPRESS_IDF_LOGS     1

// Instrumentacion del loop: publica 1 vez por segundo el tiempo MAXIMO que se
// paso en cada fase. Sirve para encontrar quien se come las ranuras. Dejar en 0
// para las corridas de medicion (agrega una linea '#' por segundo).
#define DEBUG_LOOP_TIMING     0
#define CSV_ENABLED           1     // 1 linea CSV por frame enviado
#define TELEMETRY_INTERVAL_MS 1000  // ventana de agregacion (fps, bytes/s)

// -----------------------------------------------------------------------------
// 8. TLS / AUTENTICACION  -  DESHABILITADO A PROPOSITO EN ESTA ETAPA
// -----------------------------------------------------------------------------
// La captura tiene que salir en texto plano: si se cifra, no se ve el protocolo
// y el trabajo pierde sentido. Los ganchos quedan preparados para la etapa
// siguiente; descomentar recien cuando el analisis en claro este cerrado.
//
// #define USE_TLS 1
// #define MQTT_PORT_TLS 8883
// static const char MQTT_CA_CERT[] PROGMEM = R"EOFCERT(
// -----BEGIN CERTIFICATE-----
// ...pegar aca el CA del broker...
// -----END CERTIFICATE-----
// )EOFCERT";
//
// #define MQTT_USER "usuario"
// #define MQTT_PASS "password"
// #define HTTP_AUTH_USER "admin"
// #define HTTP_AUTH_PASS "admin"

// -----------------------------------------------------------------------------
// Chequeos de coherencia
// -----------------------------------------------------------------------------
#if (STREAM_MODE != MODE_HTTP_MJPEG) && (STREAM_MODE != MODE_WEBSOCKET) && (STREAM_MODE != MODE_MQTT)
#error "STREAM_MODE invalido: usar MODE_HTTP_MJPEG, MODE_WEBSOCKET o MODE_MQTT"
#endif
#if TARGET_FPS <= 0
#error "TARGET_FPS debe ser >= 1"
#endif
