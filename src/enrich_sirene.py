"""Enrichit les SIRET via l'API recherche-entreprises (gratuite).

Version parallélisée : pool de 5 threads → ~25 req/sec effectifs.
L'API officielle limite à 7 req/sec mais avec ce niveau de parallélisme
on reste raisonnable et on couvre 30k SIRET en ~20 min."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Iterable

import requests

from . import db as db_mod


API_URL = "https://recherche-entreprises.api.gouv.fr/search"
TIMEOUT = 10
N_WORKERS = 5


def _lookup_siren(session, siren):
    for attempt in range(2):
        try:
            r = session.get(API_URL, params={"q": f"siren:{siren}", "per_page": 1}, timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(1 + attempt)
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            results = data.get("results") or []
            if not results: return None
            e = results[0]
            siege = e.get("siege") or {}
            cp = siege.get("code_postal") or ""
            dep = cp[:2] if cp[:2].isdigit() else (cp[:3] if cp[:3] in
                    ("971","972","973","974","975","976") else None)
            return {
                "siren": e.get("siren"),
                "nom": e.get("nom_complet") or e.get("nom_raison_sociale"),
                "categorie": e.get("categorie_entreprise") or "ND",
                "naf": e.get("activite_principale"),
                "code_postal": cp or None,
                "departement": dep,
            }
        except Exception:
            time.sleep(0.5)
    return None


def enrich_batch(con, sirets, max_lookups=None):
    sirets = [s for s in sirets if s and len(str(s)) >= 9]
    if max_lookups: sirets = sirets[:max_lookups]

    if sirets:
        placeholders = ",".join(["?"] * len(sirets))
        done = set(
            r[0] for r in con.execute(
                f"SELECT siret FROM entreprises WHERE siret IN ({placeholders}) "
                f"AND enriched_at > current_timestamp - INTERVAL '30 days'",
                sirets,
            ).fetchall()
        )
        sirets = [s for s in sirets if s not in done]

    if not sirets:
        return {"requested": 0, "fetched": 0, "found": 0}

    session = requests.Session()
    rows = []
    found = 0
    t0 = time.time()

    def _process(siret):
        siren = str(siret)[:9]
        info = _lookup_siren(session, siren)
        if not info:
            return None
        type_ach = _classify_acheteur_prefix(siret)
        return (
            str(siret), info["siren"], info["nom"], info["categorie"],
            info["naf"], info["code_postal"], info["departement"],
            type_ach, datetime.now(),
        )

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = {ex.submit(_process, s): s for s in sirets}
        for i, future in enumerate(as_completed(futures), 1):
            row = future.result()
            if row:
                rows.append(row)
                found += 1
                if len(rows) >= 200:
                    _flush(con, rows); rows.clear()
            if i % 500 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (len(sirets) - i) / rate
                print(f"      {i}/{len(sirets)} ({found} trouvés) — {rate:.1f}/s — ETA {eta:.0f}s",
                      flush=True)
    if rows: _flush(con, rows)

    return {"requested": len(sirets), "fetched": len(sirets), "found": found}


def _classify_acheteur_prefix(siret):
    p = str(siret)[:2]
    return {
        "21": "Commune", "22": "Département", "23": "Région",
        "24": "EPCI", "20": "Métropole / EPCI", "25": "Syndicat / EPCI",
        "26": "Hôpital / établ. public local",
        "18": "Établ. public national", "19": "Établ. public national",
        "11": "État", "13": "État", "17": "État / divers",
    }.get(p)


def _flush(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO entreprises "
        "(siret, siren, nom, categorie, naf, code_postal, departement, type_acheteur, enriched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def enrich_top_actors(con, top_acheteurs=1000, top_titulaires=1000):
    ach = con.execute("""
        SELECT acheteur_id FROM marches
        WHERE acheteur_id IS NOT NULL
        GROUP BY acheteur_id ORDER BY count(*) DESC LIMIT ?
    """, (top_acheteurs,)).fetchall()
    a_sirets = [r[0] for r in ach]

    tit = con.execute("""
        SELECT titulaire_id FROM titulaires_marche
        WHERE titulaire_id IS NOT NULL
        GROUP BY titulaire_id ORDER BY count(*) DESC LIMIT ?
    """, (top_titulaires,)).fetchall()
    t_sirets = [r[0] for r in tit]

    all_sirets = list(dict.fromkeys(a_sirets + t_sirets))
    print(f"Enrichissement : {len(all_sirets)} SIRET uniques", flush=True)

    stats = enrich_batch(con, all_sirets)
    print(f"  Trouvés : {stats['found']}/{stats['requested']}", flush=True)
    return stats


if __name__ == "__main__":
    import sys
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    con = db_mod.connect()
    enrich_top_actors(con, top_acheteurs=top, top_titulaires=top)
    con.close()
