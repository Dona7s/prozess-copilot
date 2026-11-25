"""Prozess Copilot: Text → BPMN-Diagramm → Analyse (Streamlit-Oberfläche).

Start:  streamlit run app.py
"""
from __future__ import annotations

import json
import os

import streamlit as st
import streamlit.components.v1 as components

from copilot.bpmn import safe_id, to_bpmn_xml
from copilot.demo import EXAMPLE_TEXT, DemoClient
from copilot.llm import DEFAULT_MODEL, GeminiClient, LLMError, analyze_process, extract_process
from copilot.schema import ProcessModel
from copilot.viewer import viewer_html

st.set_page_config(page_title="Prozess Copilot", page_icon="🧭", layout="wide")

KIND_DE = {"bottleneck": "Engpass", "handoff": "Übergabe", "waiting": "Wartezeit", "other": "Sonstiges"}

# ---------------------------------------------------------------- Seitenleiste
with st.sidebar:
    st.header("Einstellungen")
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    api_key = st.text_input("Gemini-API-Schlüssel", value=env_key, type="password",
                            help="Erhältlich unter https://aistudio.google.com/apikey. Ohne Schlüssel läuft der Demo-Modus.")
    model_name = st.text_input("Modell", value=DEFAULT_MODEL)
    max_repairs = st.slider("Reparaturversuche (max.)", 0, 5, 3)
    demo = st.toggle("Demo-Modus (ohne API)", value=not api_key)
    st.caption("Der Demo-Modus liefert eine vorbereitete Antwort für den Beispieltext.")


def get_client():
    if demo:
        return DemoClient()
    return GeminiClient(api_key=api_key, model=model_name)


# ---------------------------------------------------------------- 1. Eingabe
st.title("🧭 Prozess Copilot")
st.write("Beschreiben Sie einen Prozess in eigenen Worten. Der Copilot erstellt daraus ein BPMN-2.0-Diagramm "
         "und zeigt Schwachstellen sowie Automatisierungspotenziale auf.")

text = st.text_area("Prozessbeschreibung", value=st.session_state.get("text", EXAMPLE_TEXT), height=130)
col_a, col_b = st.columns([1, 5])
run = col_a.button("Diagramm erstellen", type="primary")
if col_b.button("Beispiel einfügen"):
    st.session_state["text"] = EXAMPLE_TEXT
    st.rerun()

if run:
    st.session_state["text"] = text
    for k in ("result", "analysis"):
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
xml = to_bpmn_xml(model)
analysis = st.session_state.get("analysis")

marks: dict[str, str] = {}
if analysis:
    for w in analysis.weaknesses:
        for nid in w.node_ids:
            marks[safe_id(nid)] = "weak"
    for a in analysis.automations:
        sid = safe_id(a.node_id)
        marks[sid] = (marks.get(sid, "") + " auto").strip()

st.subheader(f"Diagramm: {model.name}")
components.html(viewer_html(xml, marks), height=560)
if analysis:
    st.caption("🟧 Schwachstelle · 🟩 Automatisierungspotenzial · 🟪 beides")

d1, d2, d3 = st.columns([1, 1, 4])
fname = safe_id(model.name).lower() or "prozess"
d1.download_button("⬇️ .bpmn herunterladen", xml, file_name=f"{fname}.bpmn", mime="application/xml")
d2.download_button("⬇️ JSON", json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
                   file_name=f"{fname}.json", mime="application/json")
d3.caption("Die `.bpmn`-Datei lässt sich in Camunda Modeler, Signavio oder bpmn.io öffnen.")

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
else:
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
        use_container_width=True,
    )
