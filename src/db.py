"""Connexion DuckDB et schéma de la base de veille."""
from __future__ import annotations

import os
from pathlib import Path
import duckdb

# URL du release "latest-db" — la DB est uploadée ici par le workflow weekly
DB_RELEASE_URL = (
    "https://github.com/kzrseb/Avalanch-Veille-Dashboard/releases/"
    "download/latest-db/veille.duckdb"
)


def _resolve_db_path(read_only: bool) -> Path:
    """Détermine où stocker/lire la DB.

    - read_only=True (app Streamlit Cloud) : /tmp/veille.duckdb (le seul writable
      sur Streamlit Cloud) ; téléchargée depuis le release.
    - read_only=False (workflow GitHub Actions) : data/veille.duckdb (par défaut)
      ou VEILLE_DB env var.
    """
    if read_only:
        return Path("/tmp/veille.duckdb")
    return Path(os.environ.get("VEILLE_DB", "data/veille.duckdb"))


def ensure_db_downloaded(target: Path) -> None:
    """Télécharge la DB depuis GitHub Releases si elle n'existe pas localement."""
    if target.exists() and target.stat().st_size > 1_000_000:
        return
    import requests
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Téléchargement DB depuis {DB_RELEASE_URL}…", flush=True)
    with requests.get(DB_RELEASE_URL, stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        with target.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                fh.write(chunk)
    print(f"DB téléchargée : {target.stat().st_size/1e6:.0f} Mo", flush=True)


# DB_PATH conservée pour compat ; pointe sur la version write par défaut (workflow)
DB_PATH = _resolve_db_path(read_only=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS marches (
    id              VARCHAR,    -- pas unique : un marché alloti = plusieurs lignes même id
    source          VARCHAR,
    cpv_8           VARCHAR,
    cpv_4           VARCHAR,
    cpv_2           VARCHAR,
    objet           TEXT,
    montant         DOUBLE,
    duree_mois      DOUBLE,
    procedure       VARCHAR,   -- MAPA | AO_FORMEL | GRE_A_GRE | AUTRE
    procedure_raw   VARCHAR,
    nature          VARCHAR,   -- travaux | services | fournitures
    ccag            VARCHAR,
    date_publication DATE,
    date_notification DATE,
    date_fin_estimee DATE,
    acheteur_id     VARCHAR,
    lieu_code       VARCHAR,
    lieu_type       VARCHAR,
    departement     VARCHAR,
    offres_recues   INTEGER,
    critere_env     BOOLEAN
);

-- Index créés APRÈS ingestion (cf. db.create_indexes)

CREATE TABLE IF NOT EXISTS titulaires_marche (
    marche_id        VARCHAR,
    titulaire_id     VARCHAR,
    titulaire_type   VARCHAR
);

-- index créés en post-load

CREATE TABLE IF NOT EXISTS entreprises (
    siret           VARCHAR PRIMARY KEY,
    siren           VARCHAR,
    nom             VARCHAR,
    categorie       VARCHAR,    -- PME | ETI | GE | ND
    naf             VARCHAR,
    code_postal     VARCHAR,
    departement     VARCHAR,
    type_acheteur   VARCHAR,
    enriched_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cpv_labels (
    code     VARCHAR PRIMARY KEY,
    libelle  VARCHAR,
    niveau   INTEGER
);

CREATE TABLE IF NOT EXISTS departements (
    code VARCHAR PRIMARY KEY,
    nom  VARCHAR
);

CREATE TABLE IF NOT EXISTS recherches_sauvegardees (
    id              INTEGER,
    nom             VARCHAR,
    filtres_json    TEXT,
    email           VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    last_alert_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta (
    cle     VARCHAR PRIMARY KEY,
    valeur  VARCHAR
);
"""


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Renvoie une connexion DuckDB. Télécharge la DB depuis GitHub Releases
    si on est en read_only (= app Streamlit Cloud) et qu'elle n'existe pas."""
    db_path = _resolve_db_path(read_only)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        try:
            ensure_db_downloaded(db_path)
        except Exception as e:
            print(f"[warn] Téléchargement DB échoué : {e}", flush=True)
    return duckdb.connect(str(db_path), read_only=read_only)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Crée toutes les tables si elles n'existent pas."""
    con.execute(SCHEMA)


def create_indexes(con: duckdb.DuckDBPyConnection) -> None:
    """Crée les index sur les colonnes les plus filtrées. À appeler après ingestion."""
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_marches_cpv4 ON marches(cpv_4)",
        "CREATE INDEX IF NOT EXISTS idx_marches_cpv2 ON marches(cpv_2)",
        "CREATE INDEX IF NOT EXISTS idx_marches_acheteur ON marches(acheteur_id)",
        "CREATE INDEX IF NOT EXISTS idx_marches_dpub ON marches(date_publication)",
        "CREATE INDEX IF NOT EXISTS idx_marches_dep ON marches(departement)",
        "CREATE INDEX IF NOT EXISTS idx_marches_fin ON marches(date_fin_estimee)",
        "CREATE INDEX IF NOT EXISTS idx_tm_marche ON titulaires_marche(marche_id)",
        "CREATE INDEX IF NOT EXISTS idx_tm_tit ON titulaires_marche(titulaire_id)",
    ]:
        try:
            con.execute(stmt)
        except Exception as e:
            print(f"[warn] index skipped: {e}", flush=True)


def seed_reference(con: duckdb.DuckDBPyConnection) -> None:
    """Charge les libellés CPV (2 et 4 chiffres) et les départements."""
    from .cpv_ref import CPV4, DEPARTEMENTS
    from .ingest_decp import CPV_FAM
    rows2 = [(k, v, 2) for k, v in CPV_FAM.items()]
    rows4 = [(k, v, 4) for k, v in CPV4.items()]
    con.execute("DELETE FROM cpv_labels")
    con.executemany("INSERT INTO cpv_labels VALUES (?, ?, ?)", rows2 + rows4)

    con.execute("DELETE FROM departements")
    con.executemany("INSERT INTO departements VALUES (?, ?)",
                    [(k, v) for k, v in DEPARTEMENTS.items()])


def set_meta(con, cle: str, valeur: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (cle, valeur))


def get_meta(con, cle: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT valeur FROM meta WHERE cle = ?", (cle,)).fetchone()
    return row[0] if row else default
