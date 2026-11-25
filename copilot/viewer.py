"""Browser-Ansicht auf Basis von bpmn-js (HTML) mit Zoom und Vollbild."""
from __future__ import annotations

import json

BPMN_JS = "https://unpkg.com/bpmn-js@17.11.1/dist"

# Unterhalb dieses Zoomfaktors wäre die Schrift bei „Einpassen“ kaum lesbar.
MIN_READABLE_ZOOM = 0.75


def _js(value) -> str:
    """JSON für die Einbettung in <script> – verhindert ein vorzeitiges </script>."""
    return json.dumps(value).replace("</", "<\\/")


def viewer_html(xml: str, highlight: dict[str, str] | None = None, height: int = 640,
                min_zoom: float = MIN_READABLE_ZOOM) -> str:
    """highlight: {node_id: "weak" | "auto" | "new"} — Elemente farbig markieren.

    Bedienung: Mausrad = scrollen, Strg + Mausrad = zoomen, Ziehen = verschieben,
    dazu Schaltflächen für Zoom, Einpassen, 100 % und Vollbild.
    min_zoom: kleinster Zoom beim initialen Einpassen (0 = immer ganz einpassen).
    """
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<link rel="stylesheet" href="{BPMN_JS}/assets/bpmn-js.css">
<script src="{BPMN_JS}/bpmn-navigated-viewer.production.min.js"></script>
<style>
  html,body {{ margin:0; background:#fff; font-family: system-ui, sans-serif; }}
  #wrap {{ position:relative; height:{height}px; border:1px solid #ddd; border-radius:8px; overflow:hidden; background:#fff; }}
  #wrap:fullscreen {{ height:100vh; border:0; border-radius:0; }}
  #canvas {{ position:absolute; inset:0; }}
  #bar {{ position:absolute; top:10px; right:10px; z-index:10; display:flex; gap:4px;
          background:rgba(255,255,255,.92); border:1px solid #ddd; border-radius:8px; padding:4px; }}
  #bar button {{ min-width:34px; height:32px; border:1px solid #ccc; border-radius:6px; background:#fff;
                 cursor:pointer; font-size:15px; color:#222; padding:0 8px; }}
  #bar button:hover {{ background:#f1f3f5; }}
  #zoomlbl {{ align-self:center; font-size:12px; color:#555; min-width:42px; text-align:center; }}
  #hint {{ position:absolute; left:10px; bottom:8px; z-index:10; font-size:11px; color:#888; }}
  .weak:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#d9480f !important; stroke-width:3px !important; fill:#fff4e6 !important; }}
  .auto:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#2b8a3e !important; stroke-width:3px !important; fill:#ebfbee !important; }}
  .weak.auto:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#7048e8 !important; fill:#f3f0ff !important; }}
  .new:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#1971c2 !important; stroke-width:3px !important; fill:#e7f5ff !important; }}
  #err {{ color:#c92a2a; padding:8px; }}
</style></head>
<body>
<div id="wrap">
  <div id="canvas"></div>
  <div id="bar">
    <button id="zout" title="Verkleinern">−</button>
    <span id="zoomlbl">100 %</span>
    <button id="zin" title="Vergrößern">+</button>
    <button id="z100" title="Originalgröße">1:1</button>
    <button id="zfit" title="Ganzes Diagramm einpassen">⤢</button>
    <button id="zfull" title="Vollbild (Esc zum Beenden)">⛶</button>
  </div>
  <div id="hint">Ziehen = verschieben · Strg + Mausrad = zoomen</div>
</div>
<div id="err"></div>
<script>
  // Quelltext dieser Seite (vor dem Rendern) für den Vollbild-Fallback in einem neuen Tab
  const SRC = '<!doctype html>' + document.documentElement.outerHTML;
  const xml = {_js(xml)};
  const marks = {_js(highlight or {})};
  const MIN_ZOOM = {min_zoom};
  const viewer = new BpmnJS({{ container: '#canvas' }});
  const wrap = document.getElementById('wrap');
  let canvas;

  function label() {{
    document.getElementById('zoomlbl').textContent = Math.round(canvas.zoom() * 100) + ' %';
  }}
  // Einpassen, aber nie so klein, dass die Schrift unlesbar wird: dann links beginnen.
  let fittedW = 0;  // Breite beim letzten erfolgreichen Einpassen
  function readableFit() {{
    if (!wrap.clientWidth || !wrap.clientHeight) return;  // verborgen (z. B. inaktiver Tab)
    fittedW = wrap.clientWidth;
    canvas.resized();
    canvas.zoom('fit-viewport', 'auto');
    if (canvas.zoom() < MIN_ZOOM) {{
      const inner = canvas.viewbox().inner;
      canvas.zoom(MIN_ZOOM);
      const vb = canvas.viewbox();
      canvas.viewbox({{ x: inner.x - 20, y: inner.y + inner.height / 2 - vb.height / 2, width: vb.width, height: vb.height }});
    }}
    label();
  }}
  function zoomBy(f) {{
    if (!wrap.clientWidth) return;
    const vb = canvas.viewbox();
    canvas.zoom(canvas.zoom() * f, {{ x: vb.x + vb.width / 2, y: vb.y + vb.height / 2 }});
    label();
  }}

  viewer.importXML(xml).then(() => {{
    canvas = viewer.get('canvas');
    const reg = viewer.get('elementRegistry');
    for (const [id, cls] of Object.entries(marks)) {{
      if (reg.get(id)) cls.split(' ').forEach(c => canvas.addMarker(id, c));
    }}
    readableFit();
    viewer.get('eventBus').on('canvas.viewbox.changed', label);
    document.getElementById('zin').onclick = () => zoomBy(1.2);
    document.getElementById('zout').onclick = () => zoomBy(1 / 1.2);
    document.getElementById('z100').onclick = () => {{ canvas.zoom(1); label(); }};
    document.getElementById('zfit').onclick = () => {{
      if (!wrap.clientWidth) return;
      canvas.resized(); canvas.zoom('fit-viewport', 'auto'); label();
    }};
    document.getElementById('zfull').onclick = () => {{
      if (document.fullscreenElement) {{ document.exitFullscreen(); return; }}
      const fallback = () => {{  // falls das Einbettungs-iframe kein Vollbild erlaubt: neuer Tab
        const url = URL.createObjectURL(new Blob([SRC], {{ type: 'text/html' }}));
        window.open(url, '_blank');
      }};
      if (wrap.requestFullscreen && document.fullscreenEnabled) wrap.requestFullscreen().catch(fallback);
      else fallback();
    }};
    document.addEventListener('fullscreenchange', () => setTimeout(readableFit, 50));
    // In einem zunächst verborgenen Tab hat die Zeichenfläche die Größe 0:
    // sobald sie sichtbar wird, neu einpassen.
    new ResizeObserver(() => {{
      const w = wrap.clientWidth;
      if (w > 0 && Math.abs(w - fittedW) > 40) readableFit();
    }}).observe(wrap);
  }}).catch(e => {{ document.getElementById('err').textContent = 'Diagramm konnte nicht geladen werden: ' + e.message; }});
</script></body></html>"""
