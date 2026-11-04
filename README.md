# Zeiterfassung

Persönliche Arbeitszeiterfassung (FastAPI + SQLite, Web-UI auf Deutsch). Kein Login: nur im Tailnet betreiben.

Start lokal: siehe `CLAUDE.md`. Daten: SQLite-Datei unter `ZEIT_DB_PATH` (Container: `/data/zeit.db`, Volume mounten).
Backup: `GET /api/v1/backup`, Export: `GET /api/v1/export.csv`.
