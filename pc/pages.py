"""
pages.py  -  Las dos paginas de control, COPIADAS LITERALES del firmware.

Vienen de los R"HTML(...)HTML" de mode_http_mjpeg.cpp y mode_websocket.cpp. Se
copian en vez de reescribirse para que el equipo vea exactamente la misma
interfaz contra el ESP32 y contra una PC, y para que el HTML que viaja por el
puerto 80 sea el mismo en los dos casos (una diferencia ahi cambiaria los bytes
de la captura).

Para re-extraerlas si cambia el HTML del firmware:
    python3 regenerar_pages.py

Unico cambio respecto del original: en la pagina del modo 1 el ":81" quedo como
__PUERTO_STREAM__, porque en una PC sin privilegios el stream puede terminar en
el 8081 (ver PORT_FALLBACK_* en config.py).
"""


def pagina_mjpeg(puerto_stream):
    """La pagina del modo 1 con el puerto real del stream ya sustituido."""
    return INDEX_HTML_MJPEG.replace("__PUERTO_STREAM__", f":{puerto_stream}")


# ---------- GENERADO POR regenerar_pages.py: no editar debajo ----------

INDEX_HTML_MJPEG = r'''<!doctype html>
<meta charset="utf-8"><meta name=viewport content="width=device-width,initial-scale=1">
<title>camara_redes - MJPEG</title>
<style>body{font:14px system-ui;margin:0;background:#111;color:#eee}
header{padding:8px 12px;background:#1c1c1c}img{display:block;max-width:100%}
button{font:inherit;padding:4px 10px}pre{margin:8px 12px;color:#9c9}</style>
<header>MODO 1 &mdash; HTTP MJPEG &middot; stream en __PUERTO_STREAM__/stream
<button onclick="t()">medir /status</button></header>
<img id=v>
<pre id=s>poll apagado (no ensucia la captura)</pre>
<script>
v.src='http://'+location.hostname+'__PUERTO_STREAM__/stream';
let h=null;
function t(){if(h){clearInterval(h);h=null;s.textContent='poll apagado';return}
h=setInterval(async()=>{s.textContent=await(await fetch('/status')).text()},1000)}
</script>'''

INDEX_HTML_WS = r'''<!doctype html>
<meta charset="utf-8"><meta name=viewport content="width=device-width,initial-scale=1">
<title>camara_redes - WebSocket</title>
<style>body{font:14px system-ui;margin:0;background:#111;color:#eee}
header,.controls{padding:8px 12px;background:#1c1c1c}.controls{display:flex;gap:14px;align-items:center;border-top:1px solid #333;flex-wrap:wrap}
select,button{font:inherit;padding:4px 8px}.preview{width:min(800px,100%);aspect-ratio:4/3;background:#000}
.preview img{display:block;width:100%;height:100%;object-fit:fill}
pre{margin:8px 12px;color:#9c9}</style>
<header>MODO 2 &mdash; WebSocket binario &middot; ws://<span id=h></span>/stream</header>
<div class=controls>
<label>Resoluci&oacute;n <select id=r disabled><option value=QVGA>320&times;240</option><option value=VGA selected>640&times;480</option><option value=SVGA>800&times;600</option></select></label>
<label>FPS <select id=f disabled><option>1</option><option>2</option><option>5</option><option selected>10</option><option>15</option><option>20</option><option>25</option><option>30</option></select></label>
<button id=reset disabled>640&times;480 / 10 FPS</button><span id=c>esperando configuraci&oacute;n...</span>
</div>
<div class=preview><img id=v></div><pre id=s>conectando...</pre>
<script>
h.textContent=location.host;
let n=0,b=0,t=Date.now(),u=null;
const resolutionLabel={QVGA:'320\u00d7240',VGA:'640\u00d7480',SVGA:'800\u00d7600'};
const ws=new WebSocket('ws://'+location.host+'/stream');
ws.binaryType='blob';
function send(x){if(ws.readyState===WebSocket.OPEN)ws.send(x)}
r.onchange=()=>send('resolution:'+r.value);
f.onchange=()=>send('fps:'+f.value);
reset.onclick=()=>{send('resolution:VGA');send('fps:10')};
ws.onopen =()=>{s.textContent='conectado (101 Switching Protocols)';r.disabled=f.disabled=reset.disabled=false;send('config:get')};
ws.onclose=()=>{s.textContent='cerrado';r.disabled=f.disabled=reset.disabled=true};
ws.onmessage=e=>{
  if(typeof e.data==='string'){
    try{const m=JSON.parse(e.data);if(m.type==='config'){r.value=m.resolution;f.value=String(m.fps);c.textContent=`activo: ${resolutionLabel[m.resolution]||m.resolution} / ${m.fps} FPS`}else if(m.type==='error'){c.textContent='error: '+m.message}}
    catch(_){c.textContent=e.data}
    return;
  }
  n++; b+=e.data.size;
  if(u) URL.revokeObjectURL(u);      // sin esto el navegador se come la RAM
  u=URL.createObjectURL(e.data); v.src=u;
  const d=(Date.now()-t)/1000;
  if(d>=1){s.textContent=`${(n/d).toFixed(1)} fps  ${(b/d/1024).toFixed(0)} KiB/s`;n=0;b=0;t=Date.now()}
};
</script>'''
