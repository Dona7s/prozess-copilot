"""Prozess Copilot: Text → BPMN-Diagramm → Analyse (Streamlit-Oberfläche).

Start:  streamlit run app.py
"""
from __future__ import annotations

import json
import os

import streamlit as st

from copilot.bpmn import safe_id, to_bpmn_xml
from copilot.demo import EXAMPLE_TEXT, DemoClient
from copilot.documents import SUPPORTED, DocumentError, extract_text
from copilot.compare import compare
from copilot.llm import DEFAULT_MODEL, GeminiClient, LLMError, analyze_process, extract_process, propose_to_be
from copilot.schema import ProcessModel
from copilot.viewer import viewer_html

st.set_page_config(page_title="Prozess Copilot", page_icon="🧭", layout="wide")

SIDE_H = 380  # Höhe der Diagramme in der Nebeneinander-Ansicht
METRIC_SHORT = {
    "Aufgaben gesamt": "Aufgaben",
    "Manuelle Aufgaben": "Manuell",
    "Automatisierte Aufgaben (serviceTask)": "Automatisiert",
    "Manuelle Übergaben zwischen Rollen": "Übergaben",
    "Beteiligte Rollen": "Rollen",
}
KIND_DE = {"bottleneck": "Engpass", "handoff": "Übergabe", "waiting": "Wartezeit", "other": "Sonstiges"}

# ---------------------------------------------------------------- Seitenleiste
with st.sidebar:
    st.header("Einstellungen")
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    api_key = st.text_input("Gemini-API-Schlüssel", value=env_key, type="password",
                            help="Erhältlich unter https://aistudio.google.com/apikey. Ohne Schlüssel läuft der Demo-Modus.")
    model_name = st.text_input("Modell", value=DEFAULT_MODEL)
    max_repairs = st.slider("Reparaturversuche (max.)", 0, 5, 3)
    diagram_h = st.slider("Diagrammhöhe (px)", 400, 1100, 640, step=40)
    demo = st.toggle("Demo-Modus (ohne API)", value=not api_key)
    st.caption("Der Demo-Modus liefert eine vorbereitete Antwort für den Beispieltext.")


def show_html(html: str, height: int) -> None:
    """Eigenständige HTML-Seite einbetten (st.iframe ab neueren Streamlit-Versionen)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # Fallback für ältere Streamlit-Versionen
        import streamlit.components.v1 as components

        components.html(html, height=height)


def get_client():
    if demo:
        return DemoClient()
    return GeminiClient(api_key=api_key, model=model_name)


# ---------------------------------------------------------------- 1. Eingabe
st.title("🧭 Prozess Copilot")
st.write("Beschreiben Sie einen Prozess in eigenen Worten. Der Copilot erstellt daraus ein BPMN-2.0-Diagramm "
         "und zeigt Schwachstellen sowie Automatisierungspotenziale auf.")

with st.expander("📄 Prozessbeschreibung aus Datei laden (PDF, Word, Text)"):
    upload = st.file_uploader("Datei auswählen", type=list(SUPPORTED), label_visibility="collapsed")
    if upload is not None and st.session_state.get("upload_id") != upload.file_id:
        st.session_state["upload_id"] = upload.file_id
        try:
            doc = extract_text(upload.name, upload.getvalue())
            st.session_state["text"] = doc.text
            st.session_state["upload_info"] = (
                f"„{upload.name}“ übernommen: {len(doc.text):,} Zeichen".replace(",", ".")
                + (f", {doc.pages} Seite(n)" if doc.pages else "")
                + "".join(f" · {w}" for w in doc.warnings)
            )
            st.rerun()
        except DocumentError as e:
            st.error(str(e))
    if st.session_state.get("upload_info"):
        st.caption(st.session_state["upload_info"] + ". Der Text kann unten noch bearbeitet werden.")

text = st.text_area("Prozessbeschreibung", value=st.session_state.get("text", EXAMPLE_TEXT), height=130)
if len(text) > 4000:
    st.caption(f"{len(text):,} Zeichen. Bei langen Dokumenten am besten nur den relevanten Prozessabschnitt behalten.".replace(",", "."))
col_a, col_b = st.columns([1, 5])
run = col_a.button("Diagramm erstellen", type="primary")
if col_b.button("Beispiel einfügen"):
    st.session_state["text"] = EXAMPLE_TEXT
    st.session_state.pop("upload_info", None)
    st.rerun()

if run:
    st.session_state["text"] = text
    for k in ("result", "analysis", "tobe"):
        st.session_state.pop(k, None)
    try:
        client = get_client()
        with st.spinner("Das Modell extrahiert die Prozessstruktur …"):
            st.session_state["result"] = extract_process(client, text, max_repairs=max_repairs)
            st.session_state["client"] = client
    except LLMError as e:
        st.error(str(e))
    except Exception as e:  # Netzwerk-/API-Fehler
        st.error(f"API-Fehler: {e}")
        if "NOT_FOUND" in str(e) or "404" in str(e):
            st.info("Das Modell ist offenbar nicht (mehr) verfügbar. Bitte in der Seitenleiste unter „Modell“ "
                    "ein aktuelles Gemini-Modell eintragen, z. B. das in der Fehlermeldung empfohlene.")

result = st.session_state.get("result")
if not result:
    st.stop()

# ---------------------------------------------------------------- 2–3. JSON und Validierung
n_att = len(result.attempts)
if result.ok:
    st.success(f"Modell hat die Prüfung bestanden (nach {n_att} Versuch{'en' if n_att > 1 else ''}).")
else:
    st.error(f"Auch nach {n_att} Versuchen bleiben Fehler. Bitte die Beschreibung präzisieren.")
    for e in result.errors:
        st.write(f"• {e}")

with st.expander("Versuche und Validierungsergebnisse"):
    for i, a in enumerate(result.attempts, 1):
        st.markdown(f"**Versuch {i}:** " + ("✅ fehlerfrei" if not a["errors"] else f"❌ {len(a['errors'])} Fehler"))
        for e in a["errors"]:
            st.write(f"  • {e}")
        st.code(a["json"], language="json")

model: ProcessModel | None = result.model
if model is None:
    st.stop()

# ---------------------------------------------------------------- 4–5. BPMN

def diagram(m: ProcessModel, marks: dict[str, str], key: str, height: int, min_zoom: float | None = None) -> None:
    """Diagramm anzeigen und als .bpmn / JSON zum Download anbieten."""
    xml = to_bpmn_xml(m)
    kw = {} if min_zoom is None else {"min_zoom": min_zoom}
    show_html(viewer_html(xml, marks, height=height, **kw), height=height + 12)
    fname = safe_id(m.name).lower() or "prozess"
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ .bpmn herunterladen", xml, file_name=f"{fname}.bpmn", mime="application/xml",
                       key=f"dl_bpmn_{key}", width="stretch")
    c2.download_button("⬇️ JSON", json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=2),
                       file_name=f"{fname}.json", mime="application/json", key=f"dl_json_{key}", width="stretch")


analysis = st.session_state.get("analysis")
marks: dict[str, str] = {}
if analysis:
    for w in analysis.weaknesses:
        for nid in w.node_ids:
            marks[safe_id(nid)] = "weak"
    for a in analysis.automations:
        sid = safe_id(a.node_id)
        marks[sid] = (marks.get(sid, "") + " auto").strip()

st.subheader(f"Ist-Prozess: {model.name}")
diagram(model, marks, "ist", diagram_h)
legend = "🟧 Schwachstelle · 🟩 Automatisierungspotenzial · 🟪 beides · " if analysis else ""
st.caption(legend + "Die `.bpmn`-Datei lässt sich in Camunda Modeler, Signavio oder bpmn.io öffnen.")

# ---------------------------------------------------------------- 6. Analyse
st.subheader("Analyse")
if not analysis:
    if st.button("Schwachstellen und Automatisierung finden"):
        try:
            with st.spinner("Prozess wird analysiert …"):
                st.session_state["analysis"] = analyze_process(st.session_state["client"], model, st.session_state["text"])
            st.rerun()
        except Exception as e:
            st.error(f"Analysefehler: {e}")
    st.stop()

names = {n.id: n.name for n in model.nodes}
st.info(analysis.summary)
st.markdown("#### ⚠️ Schwachstellen")
for w in analysis.weaknesses:
    with st.container(border=True):
        st.markdown(f"**{w.title}** · _{KIND_DE.get(w.kind.value, w.kind.value)}_")
        st.write(w.explanation)
        if w.node_ids:
            st.caption("Elemente: " + ", ".join(names.get(i, i) for i in w.node_ids))
st.markdown("#### 🤖 Automatisierungspotenziale")
st.dataframe(
    [
        {
            "Aufgabe": names.get(a.node_id, a.node_id),
            "Technologie": a.technology.value,
            "Idee": a.idea,
            "Nutzen": a.benefit,
            "Aufwand": a.effort,
        }
        for a in analysis.automations
    ],
    hide_index=True,
    width="stretch",
)

# ---------------------------------------------------------------- 7. Soll-Prozess (To-Be)
st.subheader("Soll-Prozess (To-Be)")
tobe = st.session_state.get("tobe")
if not tobe:
    st.write("Auf Basis der Analyse entwirft der Copilot einen verbesserten Prozess. "
             "Er wird genauso validiert und bei Bedarf repariert wie der Ist-Prozess.")
    if st.button("Soll-Prozess erstellen", type="primary"):
        try:
            with st.spinner("Soll-Prozess wird entworfen …"):
                st.session_state["tobe"] = propose_to_be(
                    st.session_state["client"], model, analysis, st.session_state["text"], max_repairs=max_repairs
                )
            st.rerun()
        except Exception as e:
            st.error(f"Fehler beim Soll-Prozess: {e}")
    st.stop()

n_att = len(tobe.attempts)
if tobe.ok:
    st.success(f"Soll-Prozess hat die Prüfung bestanden (nach {n_att} Versuch{'en' if n_att > 1 else ''}).")
else:
    st.error(f"Auch nach {n_att} Versuchen bleiben Fehler im Soll-Prozess:")
    for e in tobe.errors:
        st.write(f"• {e}")
with st.expander("Versuche und Validierungsergebnisse (Soll-Prozess)"):
    for i, a in enumerate(tobe.attempts, 1):
        st.markdown(f"**Versuch {i}:** " + ("✅ fehlerfrei" if not a["errors"] else f"❌ {len(a['errors'])} Fehler"))
        for e in a["errors"]:
            st.write(f"  • {e}")
        st.code(a["json"], language="json")
if tobe.model is None:
    st.stop()

cmp = compare(model, tobe.model)
new_marks = {safe_id(i): "new" for i in cmp.new_ids}

tab_soll, tab_side, tab_changes = st.tabs(["Soll-Diagramm", "Ist und Soll nebeneinander", "Änderungen und Kennzahlen"])
with tab_soll:
    diagram(tobe.model, new_marks, "soll", diagram_h)
    st.caption("🟦 neu im Soll-Prozess")
with tab_side:
    left, right = st.columns(2)
    with left:
        st.markdown("**Ist-Prozess**")
        diagram(model, marks, "ist_side", SIDE_H, min_zoom=0)
    with right:
        st.markdown("**Soll-Prozess**")
        diagram(tobe.model, new_marks, "soll_side", SIDE_H, min_zoom=0)
    st.caption("Tipp: Mit ⛶ Vollbild lässt sich jedes Diagramm einzeln vergrößern.")
with tab_changes:
    st.markdown("#### Wichtigste Änderungen")
    for c in tobe.changes:
        with st.container(border=True):
            st.markdown(f"**{c.title}**")
            st.write(c.description)
            if c.addresses:
                st.caption(f"Adressiert: {c.addresses}")
    st.markdown("#### Kennzahlen Ist → Soll")
    cols = st.columns(len(cmp.metrics))
    for col, m in zip(cols, cmp.metrics):
        mode = "off" if m.lower_is_better is None else ("inverse" if m.lower_is_better else "normal")
        col.metric(METRIC_SHORT.get(m.name, m.name), m.soll, delta=m.delta if m.delta else None,
                   delta_color=mode, help=f"{m.name}: Ist {m.ist} → Soll {m.soll}")
    st.caption("Kennzahlen werden deterministisch aus beiden Modellen berechnet, nicht vom LLM geschätzt.")
    a_col, r_col = st.columns(2)
    a_col.markdown("**Neu**\n\n" + ("\n".join(f"- {x}" for x in cmp.added) or "–"))
    r_col.markdown("**Entfallen**\n\n" + ("\n".join(f"- {x}" for x in cmp.removed) or "–"))
