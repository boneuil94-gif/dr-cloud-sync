# Production Truth

## Niveaux de preuve

`CODE_EXISTS` décrit une capacité, `TESTED` un test contrôlé, `PRODUCTION_PROVEN` une observation datée de production et `NOT_PROVEN` toute absence de preuve. `FRESH` ne signifie jamais `COMPLETE`.

## Snapshot public du 10 août 2026

La capture assainie [`evidence/production-public-2026-08-10.json`](evidence/production-public-2026-08-10.json) prouve **PRODUCTION_PROVEN** pour `/health`, HTTPS, la redirection et le SHA servi : main attendu = déployé = servi `6798ab3…` (`MATCH`). CSP, `nosniff`, Referrer-Policy et frame policy sont présents. HSTS est absent. Le handshake TLS est prouvé par la requête HTTPS, mais les détails du certificat restent `UNKNOWN`.

Le schéma, worker, heartbeat, données privées, backups et funnel ne sont pas accessibles depuis la surface publique : `NOT_PROVEN`, jamais OK. Utiliser `DRCLOUD_HTTPS_URL=… EXPECTED_COMMIT=… DRCLOUD_COMMIT=… dr-cloud-sync production-evidence`. La sortie JSON exclut secrets, tokens, cookies, credentials et clés PII.

## Production Status

La route admin read-only `GET /api/production-evidence` et les commandes exposent commit, health/HTTPS, schéma/fingerprint, worker, backup, restore, rollback, fraîcheur/couverture et rapprochement. Codes UI/opérateur : `OK`, `PARTIAL`, `WARNING`, `ERROR`, `UNKNOWN`. Les alertes internes préparées couvrent health, worker, backup/restore, source, schema, couverture et rapprochement; aucune livraison externe n'est activée implicitement.

## SumUp honesty

Transactions et payouts : `WIRED` dans le runtime, mais production `NOT_PROVEN`. Merchant, fees, refunds, chargebacks et readers : `CODED_NOT_WIRED`; aucune source ne doit être présentée ready. L'absence de credential donne `NOT_CONFIGURED`, et une API inaccessible `UNAVAILABLE`.
