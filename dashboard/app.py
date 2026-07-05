"""
Dashboard de Streamlit — MLB Edge Analyzer

Correr con:
    streamlit run dashboard/app.py
(desde la raíz del proyecto, para que encuentre los módulos data/, model/, etc.)
"""

import sys
import os

# Permite importar los módulos del proyecto aunque Streamlit se ejecute
# desde la carpeta dashboard/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from main import analyze_today

st.set_page_config(page_title="MLB Edge Analyzer", layout="wide")

st.title("⚾ MLB Edge Analyzer")
st.caption("Probabilidades del modelo vs. mercado — partidos del día")

if st.button("🔄 Actualizar análisis de hoy"):
    st.cache_data.clear()


@st.cache_data(ttl=600)
def load_data():
    return analyze_today()


rows = load_data()

if not rows:
    st.warning("No hay juegos con pitchers confirmados todavía para hoy. Intenta más tarde.")
else:
    for r in rows:
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 2, 2])

            with col1:
                st.subheader(f"{r['away_team']} @ {r['home_team']}")
                st.text(f"{r['away_pitcher'] or 'TBD'}  vs  {r['home_pitcher'] or 'TBD'}")

            with col2:
                st.metric(f"Prob. modelo — {r['away_team']}", f"{r['away_model_prob']:.1%}")
                st.metric(f"Prob. modelo — {r['home_team']}", f"{r['home_model_prob']:.1%}")

            with col3:
                if r.get("away_edge") is not None:
                    st.metric(f"Edge — {r['away_team']}", f"{r['away_edge']:+.1%}")
                    st.metric(f"Edge — {r['home_team']}", f"{r['home_edge']:+.1%}")
                else:
                    st.info("Sin cuotas cargadas")

            st.text_area("Tu decisión final", key=f"decision_{r['game_pk']}", placeholder="Notas / decisión...")

    df = pd.DataFrame(rows).drop(columns=["_feature_snapshot"], errors="ignore")
    st.divider()
    st.dataframe(df, use_container_width=True)
