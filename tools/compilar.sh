#!/usr/bin/env bash
# Compila los tres modos de una, sin tocar config.h.
# Sirve para chequear que los tres siguen compilando despues de un cambio.
#
#   ./tools/compilar.sh          # los tres
#   ./tools/compilar.sh 1        # solo el modo 1
#
# Requiere arduino-cli (brew install arduino-cli). En el Arduino IDE no hace
# falta: ahi el modo se elige editando STREAM_MODE en config.h.
set -u

FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
SKETCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v arduino-cli >/dev/null || {
  echo "falta arduino-cli. Instalar con:  brew install arduino-cli" >&2
  exit 1
}

NOMBRE=(_ "MODE_HTTP_MJPEG" "MODE_WEBSOCKET" "MODE_MQTT")
if [ "$#" -eq 0 ]; then MODOS=(1 2 3); else MODOS=("$@"); fi

fallo=0
for m in "${MODOS[@]}"; do
  echo "=== modo $m (${NOMBRE[$m]}) ==="
  log=$(mktemp)
  if arduino-cli compile -b "$FQBN" \
        --build-property "compiler.cpp.extra_flags=-DSTREAM_MODE=$m" \
        --build-path "/tmp/camara_redes_build_$m" \
        "$SKETCH" >"$log" 2>&1; then
    grep -E "Sketch uses|Global variables" "$log" | sed 's/^/  /'
  else
    echo "  FALLO:"
    grep -E "error:|Error" "$log" | head -20 | sed 's/^/    /'
    fallo=1
  fi
  rm -f "$log"
  echo
done

if [ "$fallo" -eq 0 ]; then echo "OK: compila el/los modo(s) ${MODOS[*]}"; else echo "hubo fallos."; fi
exit "$fallo"
