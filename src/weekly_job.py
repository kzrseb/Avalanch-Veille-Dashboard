"""Job hebdomadaire : télécharge DECP, ingère, enrichit, envoie alertes."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

from . import db as db_mod
from . import ingest_decp, enrich_sirene, email_alerts


DECP_DATASET_URL = (
    "https://www.data.gouv.fr/api/1/datasets/donnees-essentielles-de-la-commande-publique-fichiers-consolides/"
)
DATA_DIR = Path("data")
DECP_LOCAL = DATA_DIR / "decp-latest.json"


def download_latest_decp():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[1/4] Recherche du dernier fichier DECP…", flush=True)
    r = requests.get(DECP_DATASET_URL, timeout=30)
    r.raise_for_status()
    ds = r.json()

    raw_resources = ds.get("resources") or []
    json_resources = [
        x for x in raw_resources
        if isinstance(x, dict) and (x.get("format") or "").lower() == "json"
    ]
    if not json_resources:
        raise RuntimeError("Aucune ressource JSON trouvée dans le dataset DECP")

    # Préférer le fichier consolidé (> 100 Mo). Sinon, le plus récent JSON.
    big = [x for x in json_resources if (x.get("filesize") or 0) > 100 * 1024 * 1024]
    candidates = big if big else json_resources
    candidates.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
    target = candidates[0]

    size_mo = (target.get("filesize") or 0) / 1e6
    print(f"      → {target.get('title','?')} ({size_mo:.0f} Mo)", flush=True)
    url = target["url"]
    t0 = time.time()
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with DECP_LOCAL.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)
    print(f"      OK en {time.time()-t0:.0f}s", flush=True)
    return DECP_LOCAL


def main() -> int:
    print("=== AVALANCH VEILLE — Mise à jour hebdomadaire ===", flush=True)

    try:
        download_latest_decp()
    except Exception as e:
        print(f"[err] Téléchargement DECP : {e}", flush=True)
        if not DECP_LOCAL.exists(): return 1

    print("[2/4] Ingestion DuckDB…", flush=True)
    stats = ingest_decp.ingest(DECP_LOCAL)
    print(f"      {stats['kept']:,} marchés conservés en {stats['total_s']}s", flush=True)

    print("[3/4] Enrichissement SIRENE…", flush=True)
    try:
        con = db_mod.connect()
        es = enrich_sirene.enrich_top_actors(con, top_acheteurs=1000, top_titulaires=1000)
        print(f"      {es['found']}/{es['requested']} SIRET enrichis", flush=True)
        con.close()
    except Exception as e:
        print(f"[err] SIRENE : {e}", flush=True)

    print("[4/4] Envoi alertes mail…", flush=True)
    try:
        con = db_mod.connect()
        ea = email_alerts.run_alerts(con)
        print(f"      {ea['sent']}/{ea['total_searches']} mails envoyés", flush=True)
        con.close()
    except Exception as e:
        print(f"[err] Alertes : {e}", flush=True)

    print("=== Terminé ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
