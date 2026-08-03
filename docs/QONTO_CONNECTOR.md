# Connecteur Qonto réel — lecture seule

Contrat vérifié le 31 juillet 2026 dans la documentation officielle Qonto Business API. L'adapter utilise exclusivement `GET https://thirdparty.qonto.com/v2/organization` (organisation, comptes, solde et solde autorisé) et `GET https://thirdparty.qonto.com/v2/transactions` (transactions). L'authentification accepte le couple API key `login:secret` documenté par Qonto ou un jeton OAuth sous la forme `Bearer …`. La liste utilise `current_page` et `per_page`, puis `meta.total_pages`. Les statuts reçus sont conservés ; une même `transaction_id` met à jour la ligne existante, notamment lors du passage pending vers completed.

Le client est strictement read-only : aucun virement, bénéficiaire, carte ou paiement n'est exposé. Il impose un timeout de 8 secondes par défaut, traite 401/403 comme erreurs d'authentification, respecte `Retry-After` sur 429 et applique un backoff borné sur 429/5xx/réseau. Les métadonnées persistées sont limitées à `operation_type` et `side`.

## Activation production

1. Créer dans Qonto une API key en lecture seule adaptée à l'organisation, ou une application OAuth avec les scopes de lecture des comptes et transactions.
2. Définir `QONTO_CREDENTIAL_REF=QONTO_CREDENTIAL` puis injecter **secrètement** `QONTO_CREDENTIAL`. Ne jamais placer sa valeur dans Git, l'UI ou SQLite.
3. Redémarrer les services. La source demeure `NOT_CONFIGURED` sans secret et ne devient `CONNECTED` qu'après le vrai `GET /v2/organization`.

La cadence est `DATA_HUB_BANK_INTERVAL_SECONDS` (1 800 secondes par défaut). Un backfill contrôlé est obtenu en réinitialisant le curseur du job puis en relançant le job ; l'upsert par identifiant Qonto rend cette opération idempotente.

## Activation production vérifiable
Le workflow transmet `QONTO_CREDENTIAL` au script protégé, qui établit la référence opaque `qonto.production`. Le provider reste désactivé si la résolution échoue et CONNECTED exige le GET organisation réel. Le contrôle final doit conserver uniquement comptes/transactions/counts, dates et erreurs assainies; jamais l’en-tête Authorization. Voir `CONNECTOR_ACTIVATION_AUDIT.md` pour les limites de preuve de cette livraison.

## Paramètres runtime confirmés

Le code attend `QONTO_CREDENTIAL_REF` (référence vers `QONTO_CREDENTIAL`), `QONTO_API_URL`, `QONTO_TIMEOUT_SECONDS` et `QONTO_SYNC_INTERVAL_SECONDS`. L'organisation et les comptes sont découverts par le GET `/v2/organization`; aucun identifiant de compte ou d'organisation supplémentaire n'est requis par ce contrat. Le workflow production injecte la valeur via stdin dans `drcloud.env`, consommé par web et automation-worker; elle n'est jamais affichée.
