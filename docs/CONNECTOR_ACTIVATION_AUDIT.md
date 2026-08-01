# Audit d’activation des connecteurs externes

Date de l’audit dépôt : 2026-08-01. Cet audit ne constitue **pas** une preuve de production : cette session ne dispose ni d’un accès OVH, ni des secrets GitHub, et aucun secret n’a été lu ou affiché. Les états runtime initiaux ci-dessous proviennent du relevé opérateur fourni avec l’incident; les causes sont corroborées par le code au commit de départ `b10403b`.

## Diagnostic avant correction

| Source | Provider instancié | secret_ref attendu | Secret résolu | Health | Source Data Hub | Job enregistré | Worker actif | DB partagée | Last run | Last success | Imported | Cause exacte |
|---|---:|---|---:|---|---:|---:|---:|---:|---|---|---:|---|
| Qonto BANK | conditionnel | `qonto.production` → `QONTO_CREDENTIAL_REF` | non vérifiable; relevé: non | non exécuté si secret absent | oui | oui | compose: oui; runtime non prouvé | volume déclaré; égalité non testée | jamais | jamais | 0 | secret GitHub optionnel et installation sautée quand absent |
| ShopCaisse SALES | CSV inbox uniquement | aucun secret API ventes | non applicable | aucun health API ventes | oui | oui | compose: oui; runtime non prouvé | volume déclaré; égalité non testée | jamais | jamais | 0 | aucun endpoint ventes attesté; l’API existante est uniquement catalogue, le CSV reste fallback |
| PrestaShop CATALOG | client conditionnel | `prestashop.production` | relevé: oui | aucun health catalogue au démarrage | oui | **non** | compose: oui; runtime non prouvé | volume déclaré; égalité non testée | jamais | jamais | 0 | source déclarée CONNECTED sur présence de configuration, sans health, job ni handler |
| PrestaShop SALES | non, sans états payés | `prestashop.production` | relevé: oui | non exécuté | oui | oui | compose: oui; runtime non prouvé | volume déclaré; égalité non testée | jamais | jamais | 0 | `PRESTASHOP_PAID_STATE_IDS` jamais injecté par le workflow de production |

## Correction livrée et critères de lecture

* PrestaShop n’est CONNECTED qu’après un GET authentifié de `products`; le catalogue a désormais un job et un handler GET-only.
* La policy des états payés est une variable GitHub obligatoire, validée puis écrite dans l’environnement protégé sans impression de valeur.
* Un job dû sans handler est marqué BLOCKED au lieu d’être silencieusement ignoré.
* Le worker écrit un heartbeat et un fingerprint du chemin SQLite; le diagnostic permet de comparer le chemin web au heartbeat worker sans exposer ce chemin.
* La synchronisation manuelle réutilise exactement le registre de handlers automatique (auth, permission, CSRF et AuditLog restent sur la route existante).

## État après ce commit (preuve locale, pas production)

| Source | État atteignable après configuration | Automatique | Persistance | Limite bloquante avant FRESH production |
|---|---|---:|---:|---|
| Qonto BANK | CONNECTED puis FRESH | oui | transactions, comptes, soldes, cursor, runs | credential Qonto et appel OVH réels requis |
| ShopCaisse SALES | NOT_CONFIGURED, ou PARTIAL/FRESH via inbox | oui | Sales Ledger, cursor, runs | aucun endpoint ventes ShopCaisse attesté; ne pas inventer ni déclarer API CONNECTED |
| PrestaShop CATALOG | CONNECTED puis FRESH | oui | runs, compte de lignes, last_success | déploiement et GET réel requis |
| PrestaShop SALES | CONNECTED puis FRESH | oui | Sales Ledger, cursor, runs | IDs d’états réels et sync OVH requis |

Aucune roadmap n’est modifiée. La clôture opérationnelle exige un opérateur autorisé qui déploie après validation, lance le backfill borné à 90 jours, puis joint les diagnostics assainis et les compteurs réels. Tant que cela n’est pas fait, aucune des quatre cartes ne doit être annoncée FRESH sur la seule foi des tests.
