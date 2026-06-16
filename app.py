"""Avalanch Veille — DIAGNOSTIC mode.

Cette version est minimaliste : on isole CHAQUE étape pour voir où ça pète.
Une fois le bug trouvé, on remet app_full.py.
"""
from __future__ import annotations

import traceback
import streamlit as st

st.set_page_config(page_title="Diagnostic Avalanch", layout="wide")
st.title("🔬 Diagnostic Avalanch Veille")
st.caption("Si cette page s'affiche, Streamlit charge correctement.")

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
    st.success(f"DB connectée : {db._resolve_db_path(read_only=True)}")
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

# --- Étape 4 : comptage marchés ---
st.subheader("4. Comptage marchés")
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

# --- Étape 5 : top titulaires ---
st.subheader("5. Top titulaires (la requête qui plante normalement)")
try:
    df = q.top_titulaires(con, filters=None, limit=10)
    if df is None or df.empty:
        st.warning("Renvoyé vide ou None")
    else:
        st.success(f"OK : {len(df)} lignes")
        st.dataframe(df, width="stretch")
except Exception as e:
    st.error("top_titulaires KO :")
    st.code(traceback.format_exc())
    st.stop()

st.success("✅ TOUS LES TESTS PASSÉS — le bug est ailleurs dans l'app complète.")
