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
            title = f"{r['away_team']} @ {r['home_team']}"
            if r.get("flag_review"):
                title += "  🔎"
            col1, col2, col3 = st.columns([3, 2, 2])

            with col1:
                st.subheader(title)
                st.text(f"{r['away_pitcher'] or 'TBD'}  vs  {r['home_pitcher'] or 'TBD'}")
                if r.get("market_favorite_team"):
                    st.caption(f"Favorito del mercado: **{r['market_favorite_team']}** ({r['market_favorite_prob']:.1%})")
                elif r.get("market_favorite_prob") is not None:
                    st.caption(f"Mercado en pick'em (~{r['market_favorite_prob']:.1%})")

            with col2:
                st.metric(f"Prob. modelo — {r['away_team']}", f"{r['away_model_prob']:.1%}")
                st.metric(f"Prob. modelo — {r['home_team']}", f"{r['home_model_prob']:.1%}")

            with col3:
                if r.get("away_edge") is not None:
                    st.metric(f"Edge — {r['away_team']}", f"{r['away_edge']:+.1%}")
                    st.metric(f"Edge — {r['home_team']}", f"{r['home_edge']:+.1%}")
                else:
                    st.info("Sin cuotas cargadas")

            picks = r.get("_picks", [])
            if picks:
                st.markdown("**Picks recomendados:**")
                for p in picks:
                    line_txt = f" {p['line']:+.1f}" if p.get("line") is not None else ""
                    label = f"{p['market']} → {p['selection']}{line_txt}"
                    if p.get("forced"):
                        st.caption(f"⚠️ {label} — forzado, sin edge real (EV {p['ev']:+.2f})")
                    else:
                        st.success(f"{label}  (edge {p['edge']:+.1%}, EV {p['ev']:+.2f})")

            st.text_area("Tu decisión final", key=f"decision_{r['game_pk']}", placeholder="Notas / decisión...")

    df = pd.DataFrame(rows).drop(columns=["_feature_snapshot", "_picks"], errors="ignore")
    st.divider()
    st.dataframe(df, use_container_width=True)
