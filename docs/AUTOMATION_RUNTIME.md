# Runtime d'automatisation production

Le service Docker unique `automation-worker` exécute `dr-cloud-sync automation-worker` sur le même volume `/data` que l'application. Toutes les 30 secondes il demande au Data Hub les jobs arrivés à échéance ; les transactions SQLite conditionnelles existantes garantissent qu'un job déjà `RUNNING` n'est pas repris par un second processus. Le service redémarre `unless-stopped` et possède un healthcheck SQLite.

Cadences par défaut : ShopCaisse 10 min, PrestaShop ventes 10 min, Qonto 30 min, tick 30 s. Elles sont toutes configurables dans l'environnement, hors domaine. Les imports sont idempotents et les projections sont des jobs dépendants ; une source absente bloque uniquement sa chaîne et rend la santé globale `DEGRADED`, pas l'application `DOWN`.

Le rollback conserve le volume et les migrations SQLite additives. L'ancien conteneur peut ignorer les nouvelles tables et colonnes.

## External Platforms Activation V1

Le worker ne programme que les adapters réellement configurés. Les cadences viennent de l'environnement (`SHOPCAISSE_SYNC_INTERVAL_SECONDS`, `DATA_HUB_SALES_INTERVAL_SECONDS`, `DATA_HUB_BANK_INTERVAL_SECONDS`, `SUPPLIER_SYNC_INTERVAL_SECONDS`, `SOCIAL_ANALYTICS_INTERVAL_SECONDS`) et non du domaine. Le claim transactionnel SQLite empêche deux exécutions simultanées; l'unicité des ledgers protège les reprises. 429/`Retry-After`, timeouts et backoff restent la responsabilité des clients réels. Un provider absent bloque uniquement son job et ne déclenche aucune écriture externe.
