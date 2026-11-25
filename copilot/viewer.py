"""Browser-Ansicht auf Basis von bpmn-js (HTML)."""
from __future__ import annotations

import json

BPMN_JS = "https://unpkg.com/bpmn-js@17.11.1/dist"


def _js(value) -> str:
    """JSON für die Einbettung in <script> – verhindert ein vorzeitiges </script>."""
    return json.dumps(value).replace("</", "<\\/")


def viewer_html(xml: str, highlight: dict[str, str] | None = None, height: int = 520) -> str:
    """highlight: {node_id: "weak" | "auto"} — Elemente farbig markieren."""
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<link rel="stylesheet" href="{BPMN_JS}/assets/bpmn-js.css">
<script src="{BPMN_JS}/bpmn-navigated-viewer.production.min.js"></script>
<style>
  html,body {{ margin:0; background:#fff; font-family: system-ui, sans-serif; }}
  #canvas {{ height:{height}px; border:1px solid #ddd; border-radius:8px; }}
  .weak:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#d9480f !important; stroke-width:3px !important; fill:#fff4e6 !important; }}
  .auto:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#2b8a3e !important; stroke-width:3px !important; fill:#ebfbee !important; }}
  .weak.auto:not(.djs-connection) .djs-visual > :nth-child(1) {{ stroke:#7048e8 !important; fill:#f3f0ff !important; }}
  #err {{ color:#c92a2a; padding:8px; }}
</style></head>
<body><div id="canvas"></div><div id="err"></div>
<script>
  const xml = {_js(xml)};
  const marks = {_js(highlight or {})};
  const viewer = new BpmnJS({{ container: '#canvas' }});
  viewer.importXML(xml).then(() => {{
    const canvas = viewer.get('canvas');
    canvas.zoom('fit-viewport', 'auto');
    const reg = viewer.get('elementRegistry');
    for (const [id, cls] of Object.entries(marks)) {{
      if (reg.get(id)) cls.split(' ').forEach(c => canvas.addMarker(id, c));
    }}
  }}).catch(e => {{ document.getElementById('err').textContent = 'Diagramm konnte nicht geladen werden: ' + e.message; }});
</script></body></html>"""
