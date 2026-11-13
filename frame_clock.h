#pragma once
#include <Arduino.h>
#include "metrics.h"

// =============================================================================
//  FrameClock  -  Reloj de ranuras a FPS fijo.
//
//  El objetivo NO es enviar lo mas rapido posible, es enviar a un ritmo fijo y
//  conocido. Si un frame no llega a salir en su ranura, la ranura se pierde y se
//  contabiliza como dropped. Asi el "dropped" significa lo mismo en los tres
//  modos: "frames que el FPS objetivo pedia y el transporte no pudo entregar".
// =============================================================================
class FrameClock {
 public:
  void begin(uint32_t interval_ms) {
    interval_ = interval_ms ? interval_ms : 1;
    next_     = millis();
  }

  // true => corresponde mandar un frame ahora. Consume la ranura.
  bool due() {
    uint32_t now = millis();
    if ((int32_t)(now - next_) < 0) return false;   // aritmetica con wrap-around

    uint32_t missed = (now - next_) / interval_;
    if (missed > 0) {
      metricsFrameDropped(DROP_SCHEDULE, missed);
      next_  += missed * interval_;
      slot_  += missed;              // las ranuras perdidas igual consumen numero
    }
    next_ += interval_;
    slot_++;
    return true;
  }

  // Numero de ranura de la ULTIMA llamada a due() que devolvio true.
  // Avanza tambien en las ranuras perdidas: por eso, si este numero viaja en el
  // stream, un salto en la captura ES un frame descartado, sin necesidad de
  // mirar el CSV. Es la diferencia con contar solo frames enviados, que nunca
  // deja hueco y esconde el descarte.
  uint32_t slot() const { return slot_; }

  // Reengancha el reloj al presente. Se usa cuando no hay a quien enviarle: sin
  // esto, el tiempo ocioso se contabilizaria como una montania de descartes.
  void resync() { next_ = millis(); }

 private:
  uint32_t interval_ = 100;
  uint32_t next_     = 0;
  uint32_t slot_     = 0;
};
