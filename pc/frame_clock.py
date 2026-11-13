"""
frame_clock.py  -  Espejo de frame_clock.h.

Reloj de ranuras a FPS fijo. El objetivo NO es enviar lo mas rapido posible, es
enviar a un ritmo fijo y conocido. Si un frame no llega a salir en su ranura, la
ranura se pierde y se contabiliza como dropped. Asi "dropped" significa lo mismo
en los tres modos y en las dos plataformas: "frames que el FPS objetivo pedia y
el transporte no pudo entregar".
"""
from metrics import DROP_SCHEDULE, metrics_frame_dropped, millis


class FrameClock:
    def __init__(self):
        self._interval = 100
        self._next = 0
        self._slot = 0

    def begin(self, interval_ms):
        self._interval = interval_ms if interval_ms else 1
        self._next = millis()

    def due(self):
        """True => corresponde mandar un frame ahora. Consume la ranura."""
        now = millis()
        if now < self._next:
            return False

        missed = (now - self._next) // self._interval
        if missed > 0:
            metrics_frame_dropped(DROP_SCHEDULE, missed)
            self._next += missed * self._interval
            self._slot += missed      # las ranuras perdidas igual consumen numero
        self._next += self._interval
        self._slot += 1
        return True

    def slot(self):
        """Numero de ranura de la ULTIMA llamada a due() que devolvio True.

        Avanza tambien en las ranuras perdidas: por eso, si este numero viaja en
        el stream (modo 1 lo manda en X-Slot), un salto en la captura ES un
        frame descartado, sin necesidad de mirar el CSV. Es la diferencia con
        contar solo frames enviados, que nunca deja hueco y esconde el descarte.
        """
        return self._slot

    def resync(self):
        """Reengancha el reloj al presente. Se usa cuando no hay a quien
        enviarle: sin esto, el tiempo ocioso se contabilizaria como una montania
        de descartes."""
        self._next = millis()
