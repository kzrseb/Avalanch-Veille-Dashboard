"""Enrichissement SIRENE via le fichier Parquet officiel INSEE/data.gouv.fr.

Le Parquet est lu directement par DuckDB → un seul SQL fait toute l'enrichissement.
Couverture : 100% des entreprises actives en France (~30M lignes).
Pas d'API, pas de rate limit, pas de mémoire saturée.

URL stable INSEE (Parquet StockUniteLegale, ~660 Mo, mis à jour le 1er du mois).
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import requests

from . import db as db_mod


# URL stable data.gouv.fr (resource ID stable malgré les MAJ mensuelles)
SIRENE_PARQUET_URL = "https://www.data.gouv.fr/api/1/datasets/r/350182c9-148a-46e0-8389-76c2ec1374a3"
DATA_DIR = Path("data")
SIRENE_PARQUET = DATA_DIR / "stock_unite_legale.parquet"


def download_sirene_stock():
    """Télécharge le Parquet StockUniteLegale s'il n'est pas déjà local."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SIRENE_PARQUET.exists() and SIRENE_PARQUET.stat().st_size > 100 * 1024 * 1024:
        print(f"      Parquet SIRENE déjà présent "
              f"({SIRENE_PARQUET.stat().st_size/1e6:.0f} Mo), skip.", flush=True)
        return SIRENE_PARQUET
    print(f"      Téléchargement Parquet SIRENE…", flush=True)
    t0 = time.time()
    with requests.get(SIRENE_PARQUET_URL, stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        with SIRENE_PARQUET.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                fh.write(chunk)
    print(f"      OK en {time.time()-t0:.0f}s — "
          f"{SIRENE_PARQUET.stat().st_size/1e6:.0f} Mo", flush=True)
    return SIRENE_PARQUET


# Classification acheteur via préfixe SIREN (heuristique collectivités)
TYPE_ACHETEUR_CASE = """
CASE substr(a.siret, 1, 2)
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
    ELSE NULL
END
"""


def enrich_from_stock(con) -> dict:
    """Enrichit la table entreprises depuis le Parquet SIRENE en un seul SQL.

    DuckDB lit le Parquet via `read_parquet()` et fait le JOIN nativement.
    Performance : ~30 secondes pour 80k SIRETs.
    """
    print("[3/4] Enrichissement SIRENE (Parquet bulk data.gouv.fr)…", flush=True)

    # 1. Comptage des SIRETs à enrichir (pour info)
    n_ach = con.execute(
        "SELECT count(DISTINCT acheteur_id) FROM marches WHERE acheteur_id IS NOT NULL"
    ).fetchone()[0]
    n_tit = con.execute(
        "SELECT count(DISTINCT titulaire_id) FROM titulaires_marche WHERE titulaire_id IS NOT NULL"
    ).fetchone()[0]
    print(f"      {n_ach:,} acheteurs + {n_tit:,} titulaires distincts à enrichir", flush=True)

    # 2. Download du Parquet
    download_sirene_stock()

    # 3. Reset entreprises pour repartir propre
    print("      Reset table entreprises…", flush=True)
    con.execute("DELETE FROM entreprises")

    # 4. JOIN + INSERT en un seul SQL DuckDB
    print("      JOIN Parquet × SIRETs + INSERT…", flush=True)
    t0 = time.time()
    parquet_path = str(SIRENE_PARQUET.resolve())
    con.execute(f"""
        INSERT INTO entreprises
            (siret, siren, nom, categorie, naf, code_postal, departement, type_acheteur, enriched_at)
        WITH all_sirets AS (
            SELECT DISTINCT acheteur_id AS siret FROM marches WHERE acheteur_id IS NOT NULL
            UNION
            SELECT DISTINCT titulaire_id FROM titulaires_marche WHERE titulaire_id IS NOT NULL
        )
        SELECT
            a.siret,
            substr(a.siret, 1, 9) AS siren,
            COALESCE(s.denominationUniteLegale,
                     s.nomUniteLegale || ' ' || COALESCE(s.prenom1UniteLegale, '')) AS nom,
            COALESCE(s.categorieEntreprise, 'ND') AS categorie,
            s.activitePrincipaleUniteLegale AS naf,
            NULL AS code_postal,
            NULL AS departement,
            {TYPE_ACHETEUR_CASE} AS type_acheteur,
            current_timestamp AS enriched_at
        FROM all_sirets a
        INNER JOIN read_parquet('{parquet_path}') s
            ON s.siren = substr(a.siret, 1, 9)
        WHERE length(a.siret) >= 9 AND substr(a.siret, 1, 9) ~ '^[0-9]+$'
    """)
    inserted = con.execute("SELECT count(*) FROM entreprises").fetchone()[0]
    elapsed = time.time() - t0
    print(f"      OK : {inserted:,} SIRETs enrichis en {elapsed:.0f}s", flush=True)

    # 5. Cleanup du Parquet (libère 660 Mo)
    try:
        SIRENE_PARQUET.unlink()
    except FileNotFoundError:
        pass

    return {"requested": n_ach + n_tit, "found": inserted, "inserted": inserted}


if __name__ == "__main__":
    con = db_mod.connect()
    enrich_from_stock(con)
    con.close()
