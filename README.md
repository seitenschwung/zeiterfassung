# Zeiterfassung

Persönliche Arbeitszeiterfassung (FastAPI + SQLite, Web-UI auf Deutsch). Öffentlich erreichbar, geschützt durch ein Passwort-Login (ein Nutzer).

Start lokal: siehe `CLAUDE.md`. Daten: SQLite-Datei unter `ZEIT_DB_PATH` (Container: `/data/zeit.db`, Compose-Volume `zeit-data`).
Backup: `GET /api/v1/backup`, Export: `GET /api/v1/export.csv` (beides nur mit Anmeldung).

## Passwort setzen

Das Passwort steht nur als Hash in der Umgebungsvariable `ZEIT_PASSWORD_HASH` (Format `scrypt:N:r:p:salt:hash`, ohne `$`).
Hash erzeugen (fragt das Passwort verdeckt ab, mindestens 12 Zeichen):

```
python -m app.hash_password
# oder mit dem Image:
docker run --rm -it <image> python -m app.hash_password
```

Die ausgegebene Zeile als `ZEIT_PASSWORD_HASH` in Coolify eintragen. Ohne die Variable startet die App, lehnt aber jeden Login ab.

## Weitere Einstellungen

- `ZEIT_COOKIE_SECURE=0`: Cookie ohne `Secure`-Flag, nur für lokale Entwicklung über http. Standard ist sicher.
- Sitzungen laufen 30 Tage; nach mehreren Fehlversuchen steigt die Wartezeit pro IP (Client-IP aus `X-Forwarded-For`, gesetzt von Traefik).
