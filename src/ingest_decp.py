"""Ingestion d'un fichier DECP consolidé (~900 Mo JSON) dans DuckDB."""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from .cpv_ref import CPV4, departement_from_lieu
from . import db as db_mod


CPV_FAM = {
    "03":"Agriculture, élevage, pêche","09":"Pétrole et combustibles",
    "14":"Produits miniers","15":"Produits alimentaires",
    "16":"Machines agricoles","18":"Vêtements","19":"Cuir, textiles",
    "22":"Imprimés","24":"Produits chimiques",
    "30":"Bureau et informatique (matériel)",
    "31":"Matériels électriques","32":"Radio, TV, communication",
    "33":"Matériel médical, pharma","34":"Matériel de transport",
    "35":"Sécurité, défense","37":"Instruments musique, sport",
    "38":"Équipements labo, optique","39":"Meubles, articles ménagers",
    "41":"Eau captée","42":"Machines industrielles",
    "43":"Machines mines et BTP","44":"Structures, matériaux",
    "45":"Travaux de construction","48":"Logiciels et SI",
    "50":"Réparation et entretien","51":"Services d'installation",
    "55":"Hôtellerie, restauration","60":"Transport",
    "63":"Transport auxiliaire, voyages","64":"Postal et télécoms",
    "65":"Services publics (eau, élec)","66":"Services financiers, assurance",
    "70":"Services immobiliers","71":"Architecture, ingénierie",
    "72":"Services informatiques","73":"Recherche et développement",
    "75":"Administration publique","76":"Services pétroliers/gaziers",
    "77":"Agriculture, espaces verts","79":"Services aux entreprises",
    "80":"Éducation et formation","85":"Santé et action sociale",
    "90":"Environnement, déchets","92":"Loisirs, culture, sport",
    "98":"Autres services collectifs",
}

FOURN = set("03 09 14 15 16 18 19 22 24 30 31 32 33 34 35 37 38 39 41 42 43 44 48".split())


def cpv_family(code):
    if not code: return None
    s = str(code).strip()
    if len(s) >= 2 and s[:2].isdigit() and s[:2] in CPV_FAM:
        return s[:2]
    return None


def cpv_class4(code):
    if not code: return None
    s = str(code).strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else None


def cpv_nature(fam):
    if not fam: return None
    if fam == "45": return "travaux"
    if fam in FOURN: return "fournitures"
    return "services"


def classify_procedure(p):
    if not p: return "AUTRE"
    s = p.lower()
    if "adapt" in s: return "MAPA"
    if "appel d'offres" in s or "ouvert" in s or "restreint" in s: return "AO_FORMEL"
    if "négoci" in s or "negoci" in s or "dialogue" in s or "concurrentiel" in s: return "AO_FORMEL"
    if "sans publicité" in s or "sans mise en concurrence" in s: return "GRE_A_GRE"
    return "AUTRE"


def parse_date(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError: return None


MARCHES_COLS = [
    "id", "source", "cpv_8", "cpv_4", "cpv_2", "objet",
    "montant", "duree_mois", "procedure", "procedure_raw",
    "nature", "ccag", "date_publication", "date_notification", "date_fin_estimee",
    "acheteur_id", "lieu_code", "lieu_type", "departement",
    "offres_recues", "critere_env",
]
TIT_COLS = ["marche_id", "titulaire_id", "titulaire_type"]


def ingest(src_path, *, window_days: int = 730, today=None) -> dict:
    import pandas as pd
    today = today or date.today()
    since = today - timedelta(days=window_days)
    src_path = Path(src_path)
    if not src_path.exists():
        raise FileNotFoundError(f"DECP file not found: {src_path}")

    marches_rows, tit_rows = [], []
    t0 = time.time()
    total_lines = parsed = kept = 0

    with src_path.open(encoding="utf-8") as fh:
        for line in fh:
            total_lines += 1
            s = line.strip().rstrip(",")
            if not s or s[0] != "{": continue
            try: r = json.loads(s)
            except json.JSONDecodeError: continue
            parsed += 1

            dp = parse_date(r.get("datePublicationDonnees"))
            if dp is None or dp < since or dp > today: continue
            kept += 1

            cpv8 = str(r.get("codeCPV") or "")[:8] or None
            cpv4 = cpv_class4(cpv8)
            cpv2 = cpv_family(cpv8)
            nature = cpv_nature(cpv2)

            m = r.get("montant")
            montant = float(m) if isinstance(m, (int, float)) and 0 < m <= 1e9 else None
            d = r.get("dureeMois")
            duree = float(d) if isinstance(d, (int, float)) and 0 < d <= 600 else None
            o = r.get("offresRecues")
            offres = int(o) if isinstance(o, (int, float)) and 0 <= o <= 50 else None

            proc_raw = r.get("procedure")
            proc = classify_procedure(proc_raw)

            le = r.get("lieuExecution") or {}
            lieu_code = le.get("code") if isinstance(le, dict) else None
            lieu_type = le.get("typeCode") if isinstance(le, dict) else None
            dep = departement_from_lieu(lieu_code, lieu_type)

            dn = parse_date(r.get("dateNotification"))
            fin = (dn + timedelta(days=int(duree * 30))) if (dn and duree) else None

            env = r.get("considerationsEnvironnementales") or {}
            has_env = False
            if isinstance(env, dict):
                vals = env.get("considerationEnvironnementale") or []
                has_env = any("Pas de" not in str(v) for v in vals)

            ach = (r.get("acheteur") or {}).get("id") if isinstance(r.get("acheteur"), dict) else None
            mid = str(r.get("id") or "")

            marches_rows.append((
                mid, "DECP", cpv8, cpv4, cpv2,
                (r.get("objet") or "")[:500],
                montant, duree, proc, proc_raw,
                nature, r.get("ccag"),
                dp, dn, fin,
                ach, lieu_code, lieu_type, dep,
                offres, has_env,
            ))

            tits = r.get("titulaires") or []
            if isinstance(tits, list):
                for tobj in tits:
                    if isinstance(tobj, dict):
                        inner = tobj.get("titulaire") or {}
                        tid = inner.get("id")
                        ttype = inner.get("typeIdentifiant")
                        if tid: tit_rows.append((mid, str(tid).strip(), ttype))

    parse_elapsed = time.time() - t0

    con = db_mod.connect()
    db_mod.init_schema(con)
    db_mod.seed_reference(con)
    con.execute("DELETE FROM marches")
    con.execute("DELETE FROM titulaires_marche")

    if marches_rows:
        df_m = pd.DataFrame(marches_rows, columns=MARCHES_COLS)
        con.execute("INSERT INTO marches SELECT * FROM df_m")
    if tit_rows:
        df_t = pd.DataFrame(tit_rows, columns=TIT_COLS)
        con.execute("INSERT INTO titulaires_marche SELECT * FROM df_t")

    db_mod.create_indexes(con)

    now = datetime.now().isoformat(timespec="seconds")
    db_mod.set_meta(con, "last_ingest_at", now)
    db_mod.set_meta(con, "data_since", since.isoformat())
    db_mod.set_meta(con, "data_today", today.isoformat())
    db_mod.set_meta(con, "rows_total_file", str(parsed))
    db_mod.set_meta(con, "rows_in_window", str(kept))

    con.close()
    return {
        "lines": total_lines, "parsed": parsed, "kept": kept,
        "parse_s": round(parse_elapsed, 1),
        "total_s": round(time.time() - t0, 1),
        "since": since.isoformat(), "today": today.isoformat(),
    }


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "data/decp-latest.json"
    stats = ingest(src)
    print(f"Lignes : {stats['lines']:,}, gardés : {stats['kept']:,}, durée : {stats['total_s']}s")
