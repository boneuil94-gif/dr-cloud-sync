# Administration UX

Data Hub est l'autorité de l'état runtime des connecteurs. Une carte Qonto ne devient CONNECTED/FRESH qu'après health check réel, synchronisation réussie et mise à jour durable de `last_success`/`imported_count`. Les erreurs résolues appartiennent à l'historique et ne doivent pas remplacer une erreur active.

Les diagnostics SQLite, PRAGMA et payloads assainis sont des informations avancées, masquées par défaut. Le panneau SumUp synthétise version, migrations, consommateurs de la même base et état avant d'exposer le diagnostic complet. Aucun secret n'est rendu par une API ou une vue.
