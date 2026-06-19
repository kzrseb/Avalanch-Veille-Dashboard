"""Avalanch Veille — DIAGNOSTIC mode.

Cette version isole chaque étape pour identifier ce qui plante.
Une fois le bug identifié, on remettra la version complète.
"""
from __future__ import annotations

import traceback
import streamlit as st

st.set_page_config(page_title="Diagnostic Avalanch", layout="wide")
st.title("🔬 Diagnostic Avalanch Veille")
st.caption("Si cette page s'affiche, Streamlit charge correctement. On va tester chaque étape.")

# --- Étape 1 : imports ---
st.subheader("1. Imports")
try:
    import json
    from datetime import datetime
    import pandas as pd
    import plotly.express as px
    from src import db, queries as q
    st.success(f"Imports OK · pandas {pd.__version__}")
except Exception as e:
    st.error("Imports KO :")
    st.code(traceback.format_exc())
    st.stop()

# --- Étape 2 : connexion DB ---
st.subheader("2. Connexion DB (téléchargement depuis GitHub Releases si besoin)")
try:
    @st.cache_resource
    def get_con():
        return db.connect(read_only=True)
    con = get_con()
    st.success(f"DB connectée à : {db._resolve_db_path(read_only=True)}")
except Exception as e:
    st.error("Connexion DB KO :")
    st.code(traceback.format_exc())
    st.stop()

# --- Étape 3 : lecture meta ---
st.subheader("3. Lecture des métadonnées")
try:
    meta = q.meta(con)
    st.json(meta)
except Exception as e:
    st.error("Lecture meta KO :")
    st.code(traceback.format_exc())
    st.stop()

# --- Étape 4 : comptage des tables ---
st.subheader("4. Comptage des tables")
try:
    n_marches = con.execute("SELECT count(*) FROM marches").fetchone()[0]
    n_entreprises = con.execute("SELECT count(*) FROM entreprises").fetchone()[0]
    n_titulaires = con.execute("SELECT count(*) FROM titulaires_marche").fetchone()[0]
    st.write(f"**Marchés** : {n_marches:,}")
    st.write(f"**Entreprises enrichies** : {n_entreprises:,}")
    st.write(f"**Titulaires (relations)** : {n_titulaires:,}")
except Exception as e:
    st.error("Comptage KO :")
    st.code(traceback.format_exc())
    st.stop()

# --- Étape 5 : KPIs globaux ---
st.subheader("5. KPIs globaux")
try:
    k = q.kpis(con)
    st.json(k)
except Exception as e:
    st.error("KPIs KO :")
    st.code(traceback.format_exc())
    st.stop()

# --- Étape 6 : top titulaires ---
st.subheader("6. Top titulaires (souvent le coupable)")
try:
    df = q.top_titulaires(con, filters=None, limit=10)
    if df is None or df.empty:
        st.warning(f"Renvoyé : {type(df).__name__}, empty={df.empty if df is not None else 'N/A'}")
    else:
        st.success(f"OK : {len(df)} lignes")
        st.dataframe(df, width="stretch")
except Exception as e:
    st.error("top_titulaires KO :")
    st.code(traceback.format_exc())
    st.stop()

st.success("✅ TOUS LES TESTS PASSÉS — le bug n'est pas dans ces étapes.")
