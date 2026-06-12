"""Avalanch Veille — dashboard interactif marchés publics DECP.

Lance avec :   streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from src import db, queries as q


st.set_page_config(
    page_title="Avalanch — Veille marchés publics",
    page_icon="📊",
    layout="wide",
)


@st.cache_resource
def get_con():
    return db.connect(read_only=True)


con = get_con()
meta = q.meta(con)


def fmt_int(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    return f"{int(n):,}".replace(",", " ")


def fmt_eur(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    n = float(n)
    if abs(n) >= 1e9: return f"{n/1e9:.1f} Md€"
    if abs(n) >= 1e6: return f"{n/1e6:.1f} M€"
    if abs(n) >= 1e3: return f"{n/1e3:.0f} k€"
    return f"{n:,.0f} €".replace(",", " ")


def fmt_pct(n) -> str:
    return "—" if (n is None or pd.isna(n)) else f"{n*100:.1f} %"


def fmt_num(n, unit: str = "") -> str:
    return "—" if (n is None or pd.isna(n)) else f"{n:.1f}{unit}"


# ----- Sidebar : filtres -----

st.sidebar.markdown("### Filtres")
st.sidebar.caption("Tout le dashboard se recalcule en fonction de ces filtres.")

keyword = st.sidebar.text_input(
    "Mot-clé dans l'objet du marché",
    placeholder="ex: espaces verts, voirie, logiciel…",
)

cpv2_df = con.execute("""
    SELECT code, libelle FROM cpv_labels WHERE niveau=2 ORDER BY code
""").df()
cpv2_options = ["— Tous —"] + [f"{r.code} · {r.libelle}" for r in cpv2_df.itertuples()]
cpv2_pick = st.sidebar.selectbox("Famille CPV (2 chiffres)", cpv2_options, index=0)
cpv2 = cpv2_pick.split(" ·")[0] if cpv2_pick != "— Tous —" else None

cpv4 = None
if cpv2:
    cpv4_df = con.execute("""
        SELECT m.cpv_4 AS code, c.libelle, count(*) AS n
        FROM marches m LEFT JOIN cpv_labels c ON c.code=m.cpv_4 AND c.niveau=4
        WHERE m.cpv_2 = ?
        GROUP BY m.cpv_4, c.libelle ORDER BY n DESC
    """, (cpv2,)).df()
    if not cpv4_df.empty:
        cpv4_options = ["— Tous (famille entière) —"] + [
            f"{r.code} · {r.libelle or 'Non précisé'} ({int(r.n):,})" for r in cpv4_df.itertuples()
        ]
        cpv4_pick = st.sidebar.selectbox("Détail CPV (4 chiffres)", cpv4_options, index=0)
        cpv4 = cpv4_pick.split(" ·")[0] if not cpv4_pick.startswith("— Tous") else None

dep_df = con.execute("SELECT code, nom FROM departements ORDER BY code").df()
dep_options = ["— Toute la France —"] + [f"{r.code} · {r.nom}" for r in dep_df.itertuples()]
dep_pick = st.sidebar.selectbox("Département", dep_options, index=0)
dep = dep_pick.split(" ·")[0] if dep_pick != "— Toute la France —" else None

ach_types = ["— Tous —", "Commune", "Département", "Région", "EPCI",
             "Métropole / EPCI", "Syndicat / EPCI", "Hôpital / établ. public local",
             "État", "Établ. public national", "Autre"]
ach_pick = st.sidebar.selectbox("Type d'acheteur", ach_types, index=0)
type_acheteur = None if ach_pick == "— Tous —" else ach_pick

proc_pick = st.sidebar.selectbox(
    "Procédure", ["— Toutes —", "MAPA", "AO_FORMEL", "GRE_A_GRE", "AUTRE"], index=0
)
procedure = None if proc_pick == "— Toutes —" else proc_pick

nat_pick = st.sidebar.selectbox(
    "Nature", ["— Toutes —", "travaux", "services", "fournitures"], index=0
)
nature = None if nat_pick == "— Toutes —" else nat_pick

mt_min = st.sidebar.number_input("Montant min (€)", value=0, step=10000)
mt_max = st.sidebar.number_input("Montant max (€)", value=0, step=10000,
                                  help="Laisse 0 pour aucun plafond")

active_only = st.sidebar.checkbox("Uniquement marchés en cours")

# Sélecteur de période (date de publication)
from datetime import date as _date, timedelta as _td
default_min = _date.today() - _td(days=365)
default_max = _date.today()
st.sidebar.markdown("**Période de publication**")
date_min = st.sidebar.date_input("Du", value=default_min, key="date_min")
date_max = st.sidebar.date_input("Au", value=default_max, key="date_max")

filters = {
    "cpv_2": cpv2, "cpv_4": cpv4, "keyword": keyword.strip() or None,
    "departement": dep, "type_acheteur": type_acheteur,
    "procedure": procedure, "nature": nature,
    "min_montant": mt_min if mt_min > 0 else None,
    "max_montant": mt_max if mt_max > 0 else None,
    "active_only": active_only,
    "date_min": date_min, "date_max": date_max,
}

st.sidebar.markdown("---")
with st.sidebar.expander("Sauvegarder cette recherche"):
    save_name = st.text_input("Nom de la recherche", placeholder="ex: Espaces verts IDF")
    save_email = st.text_input("Email pour les alertes hebdo",
                                value=meta.get("default_email", ""))
    if st.button("Enregistrer"):
        if save_name and save_email:
            con2 = db.connect()
            con2.execute(
                "INSERT INTO recherches_sauvegardees (id, nom, filtres_json, email) "
                "VALUES (COALESCE((SELECT max(id) FROM recherches_sauvegardees), 0)+1, ?, ?, ?)",
                (save_name, json.dumps(filters, ensure_ascii=False), save_email),
            )
            con2.close()
            st.success(f"Recherche « {save_name} » enregistrée.")
            st.cache_resource.clear()
        else:
            st.warning("Nom + email requis")


# ----- Header -----

st.markdown("# Veille marchés publics")
last_ingest = meta.get("last_ingest_at", "—")
data_window = f"{meta.get('data_since','?')} → {meta.get('data_today','?')}"
st.caption(f"Source : DECP consolidé · Dernière mise à jour : **{last_ingest}** · "
           f"Fenêtre 12 mois : {data_window}")

active = []
if cpv2: active.append(f"CPV {cpv2}" + (f"·{cpv4}" if cpv4 else ""))
if keyword: active.append(f"« {keyword} »")
if dep: active.append(f"Dép. {dep}")
if type_acheteur: active.append(type_acheteur)
if procedure: active.append(procedure)
if nature: active.append(nature.title())
if active_only: active.append("Marchés en cours")
if active:
    st.markdown("**Filtres actifs** : " + " · ".join(active))


# ----- Tabs -----

tabs = st.tabs([
    "Vue d'ensemble", "Familles CPV", "Acheteurs", "Titulaires",
    "Échéances", "Marchés", "Recherches sauvegardées",
])


with tabs[0]:
    k = q.kpis(con, filters)
    if k["n"] == 0:
        st.warning("Aucun marché ne correspond aux filtres.")
    else:
        cols = st.columns(4)
        cols[0].metric("Marchés", fmt_int(k["n"]))
        cols[1].metric("Montant médian", fmt_eur(k["med"]))
        cols[2].metric("Montant moyen", fmt_eur(k["moy"]))
        cols[3].metric("Montant total cumulé", fmt_eur(k["tot"]))
        cols = st.columns(4)
        cols[0].metric("Durée moyenne", fmt_num(k["dur"], " mois"))
        cols[1].metric("Offres / marché", fmt_num(k["offres"]))
        cols[2].metric("Critère env.", fmt_pct(k["taux_env"]))
        cols[3].metric("Taux MAPA", fmt_pct(k["taux_mapa"]))

        st.markdown("### Saisonnalité")
        s = q.saisonnalite(con, filters)
        if not s.empty:
            s["Mois"] = s["mois"].astype(int).map(
                {1:"Jan",2:"Fév",3:"Mar",4:"Avr",5:"Mai",6:"Jun",
                 7:"Jul",8:"Aoû",9:"Sep",10:"Oct",11:"Nov",12:"Déc"})
            fig = px.bar(s, x="Mois", y="nb", labels={"nb":"Marchés publiés"})
            fig.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig, width="stretch")

        cols = st.columns(2)
        with cols[0]:
            st.markdown("### Procédure")
            df = q.types_procedure(con, filters)
            if not df.empty:
                fig = px.pie(df, names="procedure", values="nb", hole=0.4)
                fig.update_layout(height=320, margin=dict(l=0,r=0,t=10,b=0))
                st.plotly_chart(fig, width="stretch")
        with cols[1]:
            st.markdown("### Type d'acheteur")
            df = q.types_acheteurs(con, filters)
            if not df.empty:
                fig = px.bar(df.head(10), x="nb", y="type", orientation="h",
                             labels={"nb":"Marchés","type":""})
                fig.update_layout(height=320, margin=dict(l=0,r=0,t=10,b=0),
                                  yaxis={"categoryorder":"total ascending"})
                st.plotly_chart(fig, width="stretch")


with tabs[1]:
    if cpv2:
        st.markdown(f"### Détail CPV à 4 chiffres dans la famille **{cpv2}**")
        df = q.par_cpv4(con, filters, limit=50)
    else:
        st.markdown("### Familles CPV (2 chiffres)")
        df = q.par_cpv2(con, filters, limit=50)
    if df.empty:
        st.info("Aucun résultat.")
    else:
        df["Montant médian"] = df["montant_median"].apply(fmt_eur)
        df["Montant moyen"] = df["montant_moyen"].apply(fmt_eur)
        df["Durée moy."] = df["duree_moyenne"].apply(lambda x: fmt_num(x, " m"))
        cols_show = ["code", "libelle", "nb", "Montant médian", "Montant moyen",
                     "Durée moy.", "pct_mapa", "pct_ao"]
        if "pct_env" in df.columns: cols_show.append("pct_env")
        rename = {"code":"CPV","libelle":"Libellé","nb":"Marchés",
                  "pct_mapa":"% MAPA","pct_ao":"% AO formel","pct_env":"% critère env."}
        st.dataframe(df[cols_show].rename(columns=rename),
                     width="stretch", hide_index=True)


with tabs[2]:
    st.markdown("### Top acheteurs (filtrés) — liste prospection")
    try:
        n_lim_a = st.slider("Nombre d'acheteurs à afficher", 50, 500, 100, step=50, key="n_ach")
        df = q.top_acheteurs(con, filters, limit=n_lim_a)
        if df.empty:
            st.info("Aucun acheteur ne correspond aux filtres.")
        else:
            df["Montant total"] = df["montant_total"].apply(fmt_eur)
            df["Montant moyen"] = df["montant_moyen"].apply(fmt_eur)
            show = ["siret", "nom", "type", "nb_marches", "Montant total",
                    "Montant moyen", "nb_cpv", "dernier"]
            st.dataframe(df[show].rename(columns={
                "siret":"SIRET","nom":"Nom","type":"Type",
                "nb_marches":"Marchés","nb_cpv":"Secteurs CPV",
                "dernier":"Dernier marché",
            }), width="stretch", hide_index=True)
            csv = df[["siret", "nom", "type", "nb_marches",
                      "montant_total", "montant_moyen", "nb_cpv", "dernier"]].to_csv(index=False).encode("utf-8")
            st.download_button("Exporter cette liste (CSV)", data=csv,
                               file_name="prospection_acheteurs.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Erreur sur l'onglet Acheteurs : {e}")


with tabs[3]:
    st.markdown("### Top titulaires (gagnants) — liste prospection")
    try:
        n_lim = st.slider("Nombre de titulaires à afficher", 50, 500, 100, step=50)
        df = q.top_titulaires(con, filters, limit=n_lim)
        if df.empty:
            st.info("Aucun titulaire ne correspond aux filtres.")
        else:
            df["Montant total gagné"] = df["montant_total"].apply(fmt_eur)
            show = ["siret", "nom", "categorie", "nb_marches_gagnes",
                    "Montant total gagné", "nb_acheteurs", "nb_cpv"]
            st.dataframe(df[show].rename(columns={
                "siret":"SIRET","nom":"Nom","categorie":"Catégorie",
                "nb_marches_gagnes":"Marchés gagnés",
                "nb_acheteurs":"Acheteurs distincts","nb_cpv":"Secteurs CPV",
            }), width="stretch", hide_index=True)
            # Export CSV (pour CRM / LinkedIn / outreach)
            csv = df[["siret", "nom", "categorie", "nb_marches_gagnes",
                      "montant_total", "nb_acheteurs", "nb_cpv"]].to_csv(index=False).encode("utf-8")
            st.download_button("Exporter cette liste (CSV)", data=csv,
                               file_name="prospection_titulaires.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Erreur sur l'onglet Titulaires : {e}")


with tabs[4]:
    try:
        days = st.slider("Fenêtre d'échéance (jours)", 30, 365, 183, step=30)
        df = q.echeances(con, filters, days=days, limit=500)
        st.markdown(f"### {len(df)} marchés finissent dans les {days} prochains jours")
        if df.empty:
            st.info("Aucun marché ne finit dans cette fenêtre.")
        else:
            df["Montant"] = df["montant"].apply(fmt_eur)
            show = ["fin", "cpv_4", "cpv_libelle", "objet",
                    "acheteur_nom", "acheteur_type", "departement", "Montant"]
            st.dataframe(df[show].rename(columns={
                "fin":"Fin estimée","cpv_4":"CPV","cpv_libelle":"Secteur",
                "objet":"Objet","acheteur_nom":"Acheteur",
                "acheteur_type":"Type","departement":"Dép.",
            }), width="stretch", hide_index=True)
    except Exception as e:
        st.error(f"Erreur sur l'onglet Échéances : {e}")


with tabs[5]:
    order = st.selectbox(
        "Trier par",
        ["Plus récents", "Montant décroissant", "Durée décroissante", "Plus d'offres reçues"],
        index=0,
    )
    order_sql = {
        "Plus récents": "date_publication DESC",
        "Montant décroissant": "montant DESC NULLS LAST",
        "Durée décroissante": "duree_mois DESC NULLS LAST",
        "Plus d'offres reçues": "offres_recues DESC NULLS LAST",
    }[order]
    df = q.marches_list(con, filters, limit=300, order_by=order_sql)
    st.markdown(f"### {len(df)} marchés (max 300)")
    if df.empty:
        st.info("Aucun marché ne correspond.")
    else:
        df["Montant"] = df["montant"].apply(fmt_eur)
        df["Durée"] = df["duree_mois"].apply(lambda x: fmt_num(x, " m"))
        show = ["date_publication", "objet", "cpv_4", "cpv_libelle",
                "acheteur_nom", "acheteur_type", "departement", "Montant",
                "Durée", "procedure", "offres_recues"]
        st.dataframe(df[show].rename(columns={
            "date_publication":"Publié le","objet":"Objet",
            "cpv_4":"CPV","cpv_libelle":"Secteur",
            "acheteur_nom":"Acheteur","acheteur_type":"Type",
            "departement":"Dép.","procedure":"Procédure",
            "offres_recues":"Offres",
        }), width="stretch", hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger ces résultats (CSV)", data=csv,
                           file_name="marches_filtres.csv", mime="text/csv")


with tabs[6]:
    st.markdown("### Mes recherches sauvegardées")
    saved = con.execute("""
        SELECT id, nom, filtres_json, email, created_at, last_alert_at
        FROM recherches_sauvegardees ORDER BY id DESC
    """).df()
    if saved.empty:
        st.info("Aucune recherche sauvegardée.")
    else:
        for _, r in saved.iterrows():
            cols = st.columns([6, 2, 2, 1])
            with cols[0]:
                st.markdown(f"**{r['nom']}** — {r['email']}")
            with cols[1]:
                st.caption(f"Créée : {r['created_at']}")
            with cols[2]:
                st.caption(f"Dernier mail : {r['last_alert_at'] or '—'}")
            with cols[3]:
                if st.button("Suppr.", key=f"del_{r['id']}"):
                    con2 = db.connect()
                    con2.execute("DELETE FROM recherches_sauvegardees WHERE id=?", (int(r["id"]),))
                    con2.close()
                    st.rerun()


st.markdown("---")
st.caption("Avalanch Veille · Données DECP · Mise à jour automatique hebdomadaire")
