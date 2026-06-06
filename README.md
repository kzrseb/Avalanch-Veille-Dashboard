# Avalanch Veille — dashboard marchés publics

Plateforme de veille sur les marchés publics français (DECP), avec :

- recherche dynamique par mot-clé, CPV (2 ou 4 chiffres), département, type d'acheteur
- top acheteurs et top titulaires avec indicateurs d'activité
- échéances des marchés en cours (anticipation des renouvellements)
- enrichissement automatique SIRENE (noms et catégorie PME/ETI/GE)
- alertes mail hebdomadaires sur recherches sauvegardées
- mise à jour automatique tous les dimanches soir (GitHub Actions)

## Stack

- **Backend** : Python 3.11+, DuckDB (analytique, embedded, ultra-rapide)
- **Frontend** : Streamlit + Plotly
- **Enrichissement** : API recherche-entreprises.api.gouv.fr (gratuite, sans clé)
- **Email** : Resend (3 000 mails/mois gratuits)
- **Hébergement** : Streamlit Community Cloud (gratuit)
- **Cron** : GitHub Actions (gratuit pour repo public)

## Déploiement

Voir détails dans la conversation Cowork. Secrets requis :
- `RESEND_API_KEY`
- `RESEND_SENDER` (ex: `dashboard@avalanch.io`)
- `VEILLE_DB` (ex: `data/veille.duckdb`)

Côté GitHub Actions : ajoute les 2 premiers dans Settings → Secrets and variables → Actions.
Côté Streamlit Cloud : ajoute les 3 dans Advanced settings → Secrets au déploiement.

## Architecture des fichiers

```
avalanch-veille/
├── app.py                       # UI Streamlit
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/
│   ├── config.toml              # thème
│   └── secrets.toml.example     # template secrets
├── .github/workflows/
│   └── weekly-update.yml        # cron dimanche
├── data/
│   └── veille.duckdb            # base, committée par GitHub Actions
└── src/
    ├── db.py                    # schéma + connexion DuckDB
    ├── ingest_decp.py           # parse JSON DECP → DuckDB
    ├── cpv_ref.py               # libellés CPV + départements
    ├── queries.py               # requêtes analytics
    ├── enrich_sirene.py         # SIRET → nom + catégorie PME/ETI/GE
    ├── email_alerts.py          # envoi Resend
    └── weekly_job.py            # orchestration job hebdo
```

## Coût : 0 €/mois.
