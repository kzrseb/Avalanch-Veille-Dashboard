"""Enrichissement via la base SIRENE complète téléchargée depuis data.gouv.fr.

Stratégie :
1. Télécharge le ZIP StockUniteLegale_utf8 (~700 Mo compressé) une fois par run.
2. Streame le CSV (30M+ lignes) ligne par ligne sans tout charger en RAM.
3. Filtre uniquement les SIRENs qui nous intéressent (titulaires + acheteurs).
4. Insert massif dans entreprises.

Robuste : 100% de coverage, pas de rate limit, durée ~10 min sur GitHub Actions.
"""
from __future__ import annotations

import csv
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from . import db as db_mod


# StockUniteLegale (utf8) sur data.gouv.fr — mis à jour mensuellement
SIRENE_ZIP_URL = "https://files.data.gouv.fr/insee-sirene/StockUniteLegale_utf8.zip"

DATA_DIR = Path("data")
SIRENE_ZIP = DATA_DIR / "stock_unite_legale.zip"
SIRENE_CSV_NAME = "StockUniteLegale_utf8.csv"


def _classify_acheteur_prefix(siret: str):
    p = str(siret)[:2]
    return {
        "21": "Commune", "22": "Département", "23": "Région",
        "24": "EPCI", "20": "Métropole / EPCI", "25": "Syndicat / EPCI",
        "26": "Hôpital / établ. public local",
        "18": "Établ. public national", "19": "Établ. public national",
        "11": "État", "13": "État", "17": "État / divers",
    }.get(p)


def download_sirene_stock():
    """Télécharge le ZIP SIRENE de data.gouv.fr s'il n'existe pas localement."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SIRENE_ZIP.exists() and SIRENE_ZIP.stat().st_size > 100 * 1024 * 1024:
        print(f"      ZIP SIRENE déjà présent ({SIRENE_ZIP.stat().st_size/1e6:.0f} Mo), skip download.",
              flush=True)
        return SIRENE_ZIP
    print(f"      Téléchargement {SIRENE_ZIP_URL}…", flush=True)
    t0 = time.time()
    with requests.get(SIRENE_ZIP_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with SIRENE_ZIP.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                fh.write(chunk)
    print(f"      OK en {time.time()-t0:.0f}s — {SIRENE_ZIP.stat().st_size/1e6:.0f} Mo", flush=True)
    return SIRENE_ZIP


def enrich_from_stock(con) -> dict:
    """Enrichit la table entreprises depuis le fichier SIRENE bulk.

    Récupère les SIRETs présents dans marches/titulaires_marche, identifie les
    SIRENs uniques, filtre le CSV SIRENE pour ne garder que ces SIRENs, et insère.
    """
    print("[3/4] Enrichissement SIRENE (stock complet data.gouv.fr)…", flush=True)

    # 1. Collecte SIRETs uniques à enrichir
    sirets = set()
    for row in con.execute("SELECT DISTINCT acheteur_id FROM marches WHERE acheteur_id IS NOT NULL").fetchall():
        sirets.add(str(row[0]).strip())
    for row in con.execute("SELECT DISTINCT titulaire_id FROM titulaires_marche WHERE titulaire_id IS NOT NULL").fetchall():
        sirets.add(str(row[0]).strip())

    # On indexe par SIREN (9 chars) car SIRENE bulk est au niveau SIREN
    siren_to_sirets: dict[str, list[str]] = {}
    for siret in sirets:
        if len(siret) >= 9 and siret[:9].isdigit():
            siren_to_sirets.setdefault(siret[:9], []).append(siret)

    target_sirens = set(siren_to_sirets.keys())
    print(f"      {len(sirets):,} SIRETs uniques → {len(target_sirens):,} SIRENs à trouver", flush=True)

    if not target_sirens:
        return {"requested": 0, "found": 0, "inserted": 0}

    # 2. Download du stock SIRENE
    zip_path = download_sirene_stock()

    # 3. Stream parse du CSV
    print("      Parsing du CSV SIRENE…", flush=True)
    t0 = time.time()
    found = 0
    inserted = 0
    rows_buffer = []
    BATCH = 5000

    with zipfile.ZipFile(zip_path) as z:
        # Trouve le bon CSV (peut s'appeler StockUniteLegale_utf8.csv)
        csv_name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not csv_name:
            raise RuntimeError("Pas de CSV trouvé dans le ZIP SIRENE")

        with z.open(csv_name) as raw:
            # Le CSV est en UTF-8, taille ~6 Go. On le wrap dans TextIOWrapper.
            import io
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(text)
            now = datetime.now()
            for i, rec in enumerate(reader, 1):
                siren = rec.get("siren", "")
                if siren not in target_sirens:
                    continue
                found += 1
                # Champs utiles : denominationUniteLegale, categorieEntreprise,
                # activitePrincipaleUniteLegale, nomenclatureActivitePrincipaleUniteLegale
                nom = (
                    rec.get("denominationUniteLegale")
                    or rec.get("nomUniteLegale")
                    or rec.get("prenomUsuelUniteLegale", "")
                ) or None
                categorie = rec.get("categorieEntreprise") or "ND"
                naf = rec.get("activitePrincipaleUniteLegale")

                # Pour chaque SIRET ayant ce SIREN, on insère une ligne
                for siret in siren_to_sirets.get(siren, []):
                    rows_buffer.append((
                        siret, siren, nom, categorie, naf,
                        None, None, _classify_acheteur_prefix(siret), now,
                    ))
                    inserted += 1

                if len(rows_buffer) >= BATCH:
                    con.executemany(
                        "INSERT OR REPLACE INTO entreprises "
                        "(siret, siren, nom, categorie, naf, code_postal, departement, type_acheteur, enriched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows_buffer,
                    )
                    rows_buffer.clear()

                if i % 2_000_000 == 0:
                    print(f"      …lu {i:,} lignes SIRENE, trouvés {found:,} cibles "
                          f"(en {time.time()-t0:.0f}s)", flush=True)

    if rows_buffer:
        con.executemany(
            "INSERT OR REPLACE INTO entreprises "
            "(siret, siren, nom, categorie, naf, code_postal, departement, type_acheteur, enriched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows_buffer,
        )

    print(f"      Trouvés {found:,}/{len(target_sirens):,} SIRENs · "
          f"{inserted:,} SIRETs enrichis en {time.time()-t0:.0f}s", flush=True)

    # Nettoyage : on supprime le ZIP pour économiser de la place dans le runner
    try:
        SIRENE_ZIP.unlink()
    except FileNotFoundError:
        pass

    return {"requested": len(target_sirens), "found": found, "inserted": inserted}


if __name__ == "__main__":
    con = db_mod.connect()
    enrich_from_stock(con)
    con.close()
