# Connecteur Qonto réel — lecture seule

Contrat audité le 10 août 2026 contre la documentation officielle Qonto Business API. L'adapter utilise exclusivement `GET https://thirdparty.qonto.com/v2/organization` et `GET https://thirdparty.qonto.com/v2/transactions`. Cette implémentation attend une **clé API d'organisation** exactement sous la forme `sign-in:secret-key` dans le header `Authorization`, sans `Basic`, `Bearer`, base64, guillemets, espaces périphériques ni retour ligne. Le premier `:` sépare le sign-in du secret; les suivants appartiennent au secret. L'organisation et les IBAN sont découverts par l'appel authentifié : aucun slug, organization id ou IBAN n'est requis en configuration. La pagination utilise `current_page`, `per_page` et `meta.total_pages`; une même `transaction_id` met à jour la ligne existante.

Le client est strictement read-only : aucun virement, bénéficiaire, carte ou paiement n'est exposé. Il impose un timeout de 8 secondes par défaut, ne rejoue jamais 401, 403 ou WAF, respecte `Retry-After` sur 429 et applique un backoff borné sur 429/5xx/réseau. Les métadonnées persistées sont limitées à `operation_type`, `side` et `fee_amount`; aucun payload brut n'est conservé. Le signe est dérivé de `side` (`debit` négatif, `credit` positif). Une devise, un montant ou un solde absent reste inconnu au lieu de devenir artificiellement EUR ou zéro.

## Activation production

1. Créer dans Qonto une API key d'organisation autorisée à lire l'organisation, les comptes et les transactions.
2. Définir `QONTO_CREDENTIAL_REF=env:QONTO_CREDENTIAL` puis injecter **secrètement** `QONTO_CREDENTIAL`. Le préfixe `env:` est résolu en mémoire par le `SecretProvider`; ne jamais placer la valeur dans Git, l'UI ou SQLite.
3. Redémarrer les services. La source demeure `NOT_CONFIGURED` sans secret et ne devient `CONNECTED` qu'après le vrai `GET /v2/organization`.

La cadence est `QONTO_SYNC_INTERVAL_SECONDS`, puis `DATA_HUB_BANK_INTERVAL_SECONDS` en repli (10 800 secondes par défaut). Un backfill contrôlé est obtenu en réinitialisant le curseur du job puis en relançant le job ; l'upsert par identifiant Qonto rend cette opération idempotente.

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

## Diagnostic runtime et reprise

La résolution conserve la priorité existante `QONTO_SECRET_REF` → `QONTO_CREDENTIAL_REF` → `env:QONTO_CREDENTIAL`. La sélection, la présence de la clé d'environnement et la résolution sont exposées uniquement par des indicateurs OUI/NON : ni nom opaque, valeur, longueur, préfixe ou empreinte du credential n'est rendu.

Une référence absente, vide ou non résolue donne `NOT_CONFIGURED` sans requête HTTP (`SECRET_REFERENCE_UNRESOLVED` si une référence explicite ne peut être résolue). Un credential résolu installe le vrai provider et exécute `GET /v2/organization` : le succès donne `CONNECTED`; tout échec donne `ERROR`/fraîcheur `ERROR`, avec les catégories `AUTH`, `SCOPE`, `WAF`, `RATE_LIMIT`, `TIMEOUT`, `NETWORK`, `HTTP`, `INVALID_RESPONSE` ou `UNKNOWN`. Le code 401 est `AUTH`, un 403 Qonto est `SCOPE`, 429 est `RATE_LIMIT`, 408/timeouts `TIMEOUT`, DNS/connexion/TLS `NETWORK`, les autres 4xx/5xx `HTTP`, et un JSON ou une structure invalide `INVALID_RESPONSE`.

**Tester maintenant** relance ce health check même après une erreur. Un succès résout l'erreur active et autorise le job BANK; seule une synchronisation et son commit durable rendent ensuite la source `FRESH`. Le provider réel est conservé après un health en erreur afin de permettre cette reprise.

### Validation en production

Après déploiement, ouvrir **Administration → Data Hub**, contrôler que Qonto indique soit `NOT_CONFIGURED` (credential réellement absent), soit `ERROR` avec sa catégorie, soit `CONNECTED`. Cliquer **Tester maintenant**; si le test passe, lancer **Synchroniser maintenant**, contrôler le Bank Ledger, puis l'état `FRESH`. Ne copier aucun payload ou credential dans un ticket d'exploitation.

## Blocage Cloudflare 1010

Le client s'identifie comme `DrCloud-OS/1.0 (+https://osdrcloud.fr)`, accepte du JSON et n'envoie aucun header de navigateur artificiel. Un `403` présentant les marqueurs Cloudflare et le code `1010` est classé `WAF / CLOUDFLARE`, jamais `AUTH`, et n'est pas rejoué automatiquement. Un 401 JSON émis par Qonto demeure `AUTH`; un 403 Qonto sans preuve Cloudflare est `SCOPE`.

Depuis le conteneur applicatif, `python -m dr_cloud_sync.qonto_diagnostic` exécute en lecture seule les contrôles DNS, TLS et HTTP sans imprimer de corps ni de secret. Si `QONTO_CREDENTIAL` est disponible dans l'environnement du processus, les variantes authentifiées utilisent exactement `Authorization: {sign-in}:{secret-key}`. La sortie est limitée aux statuts DNS/TLS, HTTP, `Server`, `cf-ray`, code Cloudflare, content type et durée.

Administration affiche le Ray ID et permet de copier un ticket support assaini. L'IP sortante doit seulement être complétée depuis un panneau Administration protégé lorsque la politique d'exploitation l'autorise; elle n'est ni découverte ni publiée par l'interface générale.

## Rotation et états opérateur

Créer la nouvelle clé dans Qonto, remplacer atomiquement le secret GitHub `QONTO_CREDENTIAL`, redéployer puis exécuter le health authentifié avant de révoquer l'ancienne clé. Ne jamais tester une clé en la collant dans un terminal partagé. Un 401 stable est `CREDENTIAL_REJECTED` et exige une nouvelle valeur; un 403 Qonto est `SCOPE_MISSING`; un 403 Cloudflare 1010 est `WAF`; réseau/timeout est `UNAVAILABLE`; toute réponse incohérente est `ERROR`. `CONNECTED` prouve un vrai GET organisation authentifié. `FORMAT_INVALID` n'émet aucune requête.

Le diagnostic n'expose que les booléens de présence/structure, `authorization_sent`, l'endpoint, le statut HTTP, la classification provider, `cf-ray`, `request-id` et la durée. Il ne sérialise jamais la valeur, sa longueur, les headers ni le corps. Le connecteur ne fournit pas de temps réel, ne crée aucune transaction et ne peut pas corriger une clé révoquée, un scope manquant ou une règle WAF : ces cas demandent respectivement une rotation Qonto, l'autorisation de lecture ou le support Qonto avec le Ray ID assaini.
