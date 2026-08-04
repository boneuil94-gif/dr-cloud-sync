# Connecteur Qonto réel — lecture seule

Contrat vérifié le 31 juillet 2026 dans la documentation officielle Qonto Business API. L'adapter utilise exclusivement `GET https://thirdparty.qonto.com/v2/organization` (organisation, comptes, solde et solde autorisé) et `GET https://thirdparty.qonto.com/v2/transactions` (transactions). L'authentification accepte le couple API key `login:secret` documenté par Qonto ou un jeton OAuth sous la forme `Bearer …`. La liste utilise `current_page` et `per_page`, puis `meta.total_pages`. Les statuts reçus sont conservés ; une même `transaction_id` met à jour la ligne existante, notamment lors du passage pending vers completed.

Le client est strictement read-only : aucun virement, bénéficiaire, carte ou paiement n'est exposé. Il impose un timeout de 8 secondes par défaut, traite 401/403 comme erreurs d'authentification, respecte `Retry-After` sur 429 et applique un backoff borné sur 429/5xx/réseau. Les métadonnées persistées sont limitées à `operation_type` et `side`.

## Activation production

1. Créer dans Qonto une API key en lecture seule adaptée à l'organisation, ou une application OAuth avec les scopes de lecture des comptes et transactions.
2. Définir `QONTO_CREDENTIAL_REF=env:QONTO_CREDENTIAL` puis injecter **secrètement** `QONTO_CREDENTIAL`. Le préfixe `env:` est résolu en mémoire par le `SecretProvider`; ne jamais placer la valeur dans Git, l'UI ou SQLite.
3. Redémarrer les services. La source demeure `NOT_CONFIGURED` sans secret et ne devient `CONNECTED` qu'après le vrai `GET /v2/organization`.

La cadence est `DATA_HUB_BANK_INTERVAL_SECONDS` (1 800 secondes par défaut). Un backfill contrôlé est obtenu en réinitialisant le curseur du job puis en relançant le job ; l'upsert par identifiant Qonto rend cette opération idempotente.

## Activation production vérifiable
Le workflow `DrCloud OS Production` utilise le job `deploy` avec `environment: production`. Il lit directement `${{ secrets.QONTO_CREDENTIAL }}` : un Repository secret reste accessible à ce job et aucun déplacement vers les Environment secrets n'est nécessaire. Le workflow transmet la valeur par l'entrée standard au script protégé, qui établit `env:QONTO_CREDENTIAL`. Le provider reste désactivé si la résolution échoue et CONNECTED exige le GET organisation réel. Le contrôle final ne journalise que OUI/NON; jamais le credential, sa longueur, son préfixe ou l'en-tête Authorization.

| Étape | Variable attendue | Source GitHub | Présente dans le code | Transmise |
|---|---|---|---:|---:|
| Job `deploy` (`environment: production`) | `QONTO_CREDENTIAL` | Repository secret `QONTO_CREDENTIAL` | oui | oui, via `env` puis stdin |
| `configure-connectors-env.sh` distant | `QONTO_CREDENTIAL` | stdin du job | oui | oui, écrite dans `drcloud.env` |
| `drcloud.env` | `QONTO_CREDENTIAL_REF`, `QONTO_CREDENTIAL`, `QONTO_API_URL` | installateur distant | oui | oui, mode `0600` |
| service web | les trois variables runtime | `env_file: ./drcloud.env` | oui | oui, vérifié après redémarrage |
| `automation-worker` | les trois variables runtime | `env_file: ./drcloud.env` | oui | oui, vérifié après redémarrage |
| `EnvironmentSecretProvider` | `env:QONTO_CREDENTIAL` | environnement du conteneur | oui | oui, résolution contrôlée sans valeur |
| `QontoBankProvider` | credential résolu | `EnvironmentSecretProvider` | oui | oui si non vide |

## Paramètres runtime confirmés

Le code attend `QONTO_CREDENTIAL_REF` (référence vers `QONTO_CREDENTIAL`), `QONTO_API_URL`, `QONTO_TIMEOUT_SECONDS` et `QONTO_SYNC_INTERVAL_SECONDS`. L'organisation et les comptes sont découverts par le GET `/v2/organization`; aucun identifiant de compte ou d'organisation supplémentaire n'est requis par ce contrat. Le workflow production injecte la valeur via stdin dans `drcloud.env`, consommé par web et automation-worker; elle n'est jamais affichée.

## Validation après merge (manuelle)

1. Merger la PR sans lancer de déploiement manuel.
2. Attendre le workflow **DrCloud OS Production** déclenché après le CI de `main`.
3. Vérifier que le workflow est vert et que ses contrôles Web/Worker affichent seulement `OUI`.
4. Ouvrir **Administration → Data Hub**.
5. Cliquer **Tester maintenant** sur Qonto.
6. Attendre un temps d'exécution réel supérieur à `0 ms`.
7. Vérifier un état `CONNECTED` ou `ERROR AUTH`, jamais `NOT_CONFIGURED` lorsque le secret est présent.
8. Lancer **Synchroniser maintenant**.
9. Vérifier les écritures du Bank Ledger et l'état `CONNECTED · FRESH`.
