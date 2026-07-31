# Runtime d'automatisation production

Le service Docker unique `automation-worker` exécute `dr-cloud-sync automation-worker` sur le même volume `/data` que l'application. Toutes les 30 secondes il demande au Data Hub les jobs arrivés à échéance ; les transactions SQLite conditionnelles existantes garantissent qu'un job déjà `RUNNING` n'est pas repris par un second processus. Le service redémarre `unless-stopped` et possède un healthcheck SQLite.

Cadences par défaut : ShopCaisse 10 min, PrestaShop ventes 10 min, Qonto 30 min, tick 30 s. Elles sont toutes configurables dans l'environnement, hors domaine. Les imports sont idempotents et les projections sont des jobs dépendants ; une source absente bloque uniquement sa chaîne et rend la santé globale `DEGRADED`, pas l'application `DOWN`.

Le rollback conserve le volume et les migrations SQLite additives. L'ancien conteneur peut ignorer les nouvelles tables et colonnes.
