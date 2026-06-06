"""Couche analytics : requêtes DuckDB qui alimentent l'UI Streamlit."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import db as db_mod


ACHETEUR_TYPE_SQL = """
CASE substr(acheteur_id, 1, 2)
  WHEN '21' THEN 'Commune'
  WHEN '22' THEN 'Département'
  WHEN '23' THEN 'Région'
  WHEN '24' THEN 'EPCI'
  WHEN '20' THEN 'Métropole / EPCI'
  WHEN '25' THEN 'Syndicat / EPCI'
  WHEN '26' THEN 'Hôpital / établ. public local'
  WHEN '18' THEN 'Établ. public national'
  WHEN '19' THEN 'Établ. public national'
  WHEN '11' THEN 'État'
  WHEN '13' THEN 'État'
  WHEN '17' THEN 'État / divers'
  ELSE 'Autre'
END
"""


def _filter_clauses(f: dict | None, alias: str = "m") -> tuple[str, list]:
    f = f or {}
    a = f"{alias}." if alias else ""
    parts: list[str] = ["1=1"]
    params: list = []

    if f.get("cpv_2"):
        parts.append(f"{a}cpv_2 = ?")
        params.append(f["cpv_2"])
    if f.get("cpv_4"):
        parts.append(f"{a}cpv_4 = ?")
        params.append(f["cpv_4"])
    if f.get("keyword"):
        parts.append(f"{a}objet ILIKE ?")
        params.append(f"%{f['keyword']}%")
    if f.get("departement"):
        parts.append(f"{a}departement = ?")
        params.append(f["departement"])
    if f.get("procedure"):
        parts.append(f"{a}procedure = ?")
        params.append(f["procedure"])
    if f.get("nature"):
        parts.append(f"{a}nature = ?")
        params.append(f["nature"])
    if f.get("type_acheteur"):
        ach_sql = ACHETEUR_TYPE_SQL.replace("acheteur_id", f"{a}acheteur_id")
        parts.append(f"{ach_sql} = ?")
        params.append(f["type_acheteur"])
    if f.get("min_montant") is not None:
        parts.append(f"{a}montant >= ?")
        params.append(float(f["min_montant"]))
    if f.get("max_montant") is not None:
        parts.append(f"{a}montant <= ?")
        params.append(float(f["max_montant"]))
    if f.get("active_only"):
        parts.append(f"{a}date_fin_estimee >= current_date")
    if f.get("ending_within_days"):
        parts.append(f"{a}date_fin_estimee BETWEEN current_date "
                     "AND current_date + INTERVAL '%d days'" % int(f["ending_within_days"]))

    return " AND ".join(parts), params


def kpis(con, filters: dict | None = None) -> dict:
    where, params = _filter_clauses(filters)
    row = con.execute(f"""
        SELECT count(*) AS n, median(montant) AS med, avg(montant) AS moy,
            sum(montant) AS tot, avg(duree_mois) AS dur, avg(offres_recues) AS offres,
            avg(CASE WHEN procedure='MAPA' THEN 1.0 ELSE 0 END) AS taux_mapa,
            avg(CASE WHEN procedure='AO_FORMEL' THEN 1.0 ELSE 0 END) AS taux_ao,
            avg(CASE WHEN procedure='GRE_A_GRE' THEN 1.0 ELSE 0 END) AS taux_gre,
            avg(CASE WHEN critere_env THEN 1.0 ELSE 0 END) AS taux_env
        FROM marches m WHERE {where}
    """, params).fetchone()
    keys = ["n", "med", "moy", "tot", "dur", "offres",
            "taux_mapa", "taux_ao", "taux_gre", "taux_env"]
    return dict(zip(keys, row))


def par_cpv2(con, filters: dict | None = None, limit: int = 50) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT m.cpv_2 AS code, c.libelle AS libelle, count(*) AS nb,
            median(m.montant) AS montant_median, avg(m.montant) AS montant_moyen,
            sum(m.montant) AS montant_total, avg(m.duree_mois) AS duree_moyenne,
            100.0*avg(CASE WHEN m.procedure='MAPA' THEN 1.0 ELSE 0 END) AS pct_mapa,
            100.0*avg(CASE WHEN m.procedure='AO_FORMEL' THEN 1.0 ELSE 0 END) AS pct_ao,
            100.0*avg(CASE WHEN m.critere_env THEN 1.0 ELSE 0 END) AS pct_env
        FROM marches m LEFT JOIN cpv_labels c ON c.code = m.cpv_2 AND c.niveau = 2
        WHERE {where} AND m.cpv_2 IS NOT NULL
        GROUP BY m.cpv_2, c.libelle ORDER BY nb DESC LIMIT {limit}
    """, params).df()


def par_cpv4(con, filters: dict | None = None, limit: int = 50) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT m.cpv_4 AS code, c.libelle AS libelle,
            substr(m.cpv_4, 1, 2) AS famille, count(*) AS nb,
            median(m.montant) AS montant_median, avg(m.montant) AS montant_moyen,
            sum(m.montant) AS montant_total, avg(m.duree_mois) AS duree_moyenne,
            100.0*avg(CASE WHEN m.procedure='MAPA' THEN 1.0 ELSE 0 END) AS pct_mapa
        FROM marches m LEFT JOIN cpv_labels c ON c.code = m.cpv_4 AND c.niveau = 4
        WHERE {where} AND m.cpv_4 IS NOT NULL
        GROUP BY m.cpv_4, c.libelle ORDER BY nb DESC LIMIT {limit}
    """, params).df()


def top_acheteurs(con, filters: dict | None = None, limit: int = 50) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT m.acheteur_id AS siret, {ACHETEUR_TYPE_SQL} AS type,
            COALESCE(e.nom, '(nom non enrichi)') AS nom,
            count(*) AS nb_marches, sum(m.montant) AS montant_total,
            avg(m.montant) AS montant_moyen,
            count(DISTINCT m.cpv_4) AS nb_cpv,
            count(DISTINCT tm.titulaire_id) AS nb_titulaires,
            max(m.date_publication) AS dernier
        FROM marches m
        LEFT JOIN titulaires_marche tm ON tm.marche_id = m.id
        LEFT JOIN entreprises e ON e.siret = m.acheteur_id
        WHERE {where} AND m.acheteur_id IS NOT NULL
        GROUP BY m.acheteur_id, type, e.nom
        ORDER BY nb_marches DESC LIMIT {limit}
    """, params).df()


def top_titulaires(con, filters: dict | None = None, limit: int = 50) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT tm.titulaire_id AS siret,
            COALESCE(e.nom, '(nom non enrichi)') AS nom,
            COALESCE(e.categorie, 'ND') AS categorie,
            count(*) AS nb_marches_gagnes, sum(m.montant) AS montant_total,
            count(DISTINCT m.acheteur_id) AS nb_acheteurs,
            count(DISTINCT m.cpv_4) AS nb_cpv
        FROM marches m INNER JOIN titulaires_marche tm ON tm.marche_id = m.id
        LEFT JOIN entreprises e ON e.siret = tm.titulaire_id
        WHERE {where} AND tm.titulaire_id IS NOT NULL
        GROUP BY tm.titulaire_id, e.nom, e.categorie
        ORDER BY nb_marches_gagnes DESC LIMIT {limit}
    """, params).df()


def saisonnalite(con, filters: dict | None = None) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT extract(month from date_publication) AS mois, count(*) AS nb
        FROM marches m WHERE {where} AND date_publication IS NOT NULL
        GROUP BY mois ORDER BY mois
    """, params).df()


def types_acheteurs(con, filters: dict | None = None) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT {ACHETEUR_TYPE_SQL} AS type, count(*) AS nb
        FROM marches m WHERE {where} GROUP BY type ORDER BY nb DESC
    """, params).df()


def types_procedure(con, filters: dict | None = None) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT procedure, count(*) AS nb
        FROM marches m WHERE {where} GROUP BY procedure ORDER BY nb DESC
    """, params).df()


def departements(con, filters: dict | None = None, limit: int = 30) -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT m.departement AS code, d.nom AS nom, count(*) AS nb,
            sum(m.montant) AS montant_total, median(m.montant) AS montant_median
        FROM marches m LEFT JOIN departements d ON d.code = m.departement
        WHERE {where} AND m.departement IS NOT NULL
        GROUP BY m.departement, d.nom ORDER BY nb DESC LIMIT {limit}
    """, params).df()


def marches_list(con, filters: dict | None = None, limit: int = 200,
                 order_by: str = "date_publication DESC") -> pd.DataFrame:
    where, params = _filter_clauses(filters)
    return con.execute(f"""
        SELECT m.id, m.objet, m.cpv_8, m.cpv_4, c.libelle AS cpv_libelle,
            m.montant, m.duree_mois, m.procedure, m.nature,
            m.date_publication, m.date_notification, m.date_fin_estimee,
            m.acheteur_id, {ACHETEUR_TYPE_SQL} AS acheteur_type,
            COALESCE(ea.nom, '(non enrichi)') AS acheteur_nom,
            m.departement, m.offres_recues, m.critere_env
        FROM marches m
        LEFT JOIN cpv_labels c ON c.code = m.cpv_4 AND c.niveau = 4
        LEFT JOIN entreprises ea ON ea.siret = m.acheteur_id
        WHERE {where} ORDER BY {order_by} LIMIT {limit}
    """, params).df()


def echeances(con, filters: dict | None = None, days: int = 183, limit: int = 500) -> pd.DataFrame:
    fcopy = dict(filters or {})
    fcopy["ending_within_days"] = days
    where, params = _filter_clauses(fcopy)
    return con.execute(f"""
        SELECT m.date_fin_estimee AS fin, m.objet,
            m.cpv_4, c.libelle AS cpv_libelle,
            m.acheteur_id, {ACHETEUR_TYPE_SQL} AS acheteur_type,
            COALESCE(ea.nom, '(non enrichi)') AS acheteur_nom,
            m.montant, m.departement
        FROM marches m
        LEFT JOIN cpv_labels c ON c.code = m.cpv_4 AND c.niveau = 4
        LEFT JOIN entreprises ea ON ea.siret = m.acheteur_id
        WHERE {where} ORDER BY fin LIMIT {limit}
    """, params).df()


def cpv_suggestions(con, q: str, limit: int = 20) -> pd.DataFrame:
    return con.execute("""
        SELECT code, libelle, niveau FROM cpv_labels
        WHERE lower(libelle) LIKE ?
        ORDER BY niveau, libelle LIMIT ?
    """, (f"%{q.lower()}%", limit)).df()


def meta(con) -> dict:
    rows = con.execute("SELECT cle, valeur FROM meta").fetchall()
    return dict(rows)
