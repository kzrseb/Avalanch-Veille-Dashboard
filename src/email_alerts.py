"""Envoi d'alertes mail hebdo via Resend."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import requests

from . import db as db_mod, queries as q


RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, html: str, sender: str | None = None) -> dict:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY non défini dans l'environnement")
    sender = sender or os.environ.get("RESEND_SENDER", "dashboard@avalanch.io")
    r = requests.post(
        RESEND_API_URL,
        json={"from": sender, "to": [to], "subject": subject, "html": html},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _render_alert_html(saved_name, filters, df):
    n = len(df)
    rows_html = ""
    for _, r in df.head(50).iterrows():
        montant = f"{int(r['montant']):,} €".replace(",", " ") if r.get("montant") else "—"
        rows_html += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;color:#666">{r.get('date_publication') or '—'}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{(r.get('objet') or '')[:120]}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;font-size:12px">{r.get('acheteur_nom') or r.get('acheteur_id') or '—'}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-variant-numeric:tabular-nums">{montant}</td>
        </tr>"""
    tags = []
    if filters.get("cpv_2"): tags.append(f"CPV {filters['cpv_2']}")
    if filters.get("cpv_4"): tags.append(f"·{filters['cpv_4']}")
    if filters.get("keyword"): tags.append(f"« {filters['keyword']} »")
    if filters.get("departement"): tags.append(f"Dép. {filters['departement']}")
    if filters.get("type_acheteur"): tags.append(filters['type_acheteur'])
    tags_html = " · ".join(tags) or "(aucun filtre)"

    return f"""<!doctype html>
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a1a;background:#fafafa;margin:0;padding:24px">
      <div style="max-width:720px;margin:0 auto;background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:24px">
        <h1 style="margin:0 0 4px;font-size:20px;color:#0a6b3b">Veille marchés publics</h1>
        <p style="margin:0 0 4px;color:#666;font-size:13px">Recherche : <strong>{saved_name}</strong></p>
        <p style="margin:0 0 20px;color:#666;font-size:12px">{tags_html}</p>
        <p style="font-size:14px"><strong>{n}</strong> nouveau(x) marché(s) cette semaine.</p>
        <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:13px">
          <thead><tr style="background:#f3f3f3;text-align:left">
            <th style="padding:8px">Publié</th><th style="padding:8px">Objet</th>
            <th style="padding:8px">Acheteur</th><th style="padding:8px;text-align:right">Montant</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </body></html>"""


def run_alerts(con, dry_run=False):
    saved = con.execute("""
        SELECT id, nom, filtres_json, email, last_alert_at
        FROM recherches_sauvegardees
    """).fetchall()

    sent = skipped = 0
    for sid, nom, filtres_json, email, last_alert in saved:
        try: filters = json.loads(filtres_json)
        except Exception:
            skipped += 1; continue

        if last_alert:
            try: since = datetime.fromisoformat(str(last_alert)).date()
            except ValueError: since = (datetime.now() - timedelta(days=7)).date()
        else:
            since = (datetime.now() - timedelta(days=7)).date()

        where, params = q._filter_clauses(filters)
        df = con.execute(f"""
            SELECT m.date_publication, m.objet, m.cpv_4, m.montant,
                   m.acheteur_id, COALESCE(e.nom, m.acheteur_id) AS acheteur_nom
            FROM marches m
            LEFT JOIN entreprises e ON e.siret = m.acheteur_id
            WHERE {where} AND m.date_publication >= ?
            ORDER BY m.date_publication DESC LIMIT 200
        """, params + [since.isoformat()]).df()

        if df.empty: skipped += 1; continue

        html = _render_alert_html(nom, filters, df)
        subject = f"[Veille] {len(df)} nouveaux marchés — {nom}"

        if dry_run:
            print(f"[DRY] {email} ← {subject}")
        else:
            try:
                send_email(email, subject, html)
                con.execute(
                    "UPDATE recherches_sauvegardees SET last_alert_at = current_timestamp WHERE id = ?",
                    (sid,),
                )
                sent += 1
            except Exception as e:
                print(f"[err] envoi {email} : {e}", flush=True)
                skipped += 1

    return {"sent": sent, "skipped": skipped, "total_searches": len(saved)}


if __name__ == "__main__":
    import sys
    con = db_mod.connect()
    res = run_alerts(con, dry_run="--dry-run" in sys.argv)
    print(res)
    con.close()
