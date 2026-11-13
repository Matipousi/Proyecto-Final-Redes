#pragma once
#include "esp_camera.h"

// =============================================================================
//  FbGuard  -  RAII sobre el frame buffer de la camara.
//
//  Trampa clasica: si algun camino de salida se olvida de esp_camera_fb_return()
//  el pool de fb_count buffers se agota y la captura se cuelga despues de N
//  frames. En vez de confiar en acordarse en cada return/continue/error, el
//  destructor lo garantiza estructuralmente: el buffer se devuelve al salir del
//  scope, pase lo que pase.
//
//  Uso:
//      FbGuard fb(esp_camera_fb_get());
//      if (!fb) { ...; return; }          // <- devuelto igual
//      client.write(fb->buf, fb->len);
// =============================================================================
class FbGuard {
 public:
  explicit FbGuard(camera_fb_t* fb) : fb_(fb) {}
  ~FbGuard() { if (fb_) esp_camera_fb_return(fb_); }

  FbGuard(const FbGuard&)            = delete;
  FbGuard& operator=(const FbGuard&) = delete;

  camera_fb_t* get() const        { return fb_; }
  camera_fb_t* operator->() const { return fb_; }
  explicit operator bool() const  { return fb_ != nullptr; }

 private:
  camera_fb_t* fb_;
};
