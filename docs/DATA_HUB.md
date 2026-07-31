# Data Hub et runtime de synchronisation V1

## Architecture et autorités

Le Data Hub est le **plan de contrôle provider-neutral** des lectures externes. Il réutilise SQLite et les jobs applicatifs : `data_sources`, `sync_jobs` et `data_hub_sync_runs` conservent configuration, curseur, exécutions, erreurs assainies et verrou. Il ne remplace ni le Sales Ledger, ni le Bank Ledger, ni le Stock Ledger.

Sources déclarées : `SHOPCAISSE_SALES`, `PRESTASHOP_SALES`, `PRESTASHOP_CATALOG`, `BANK`, `PURCHASES`, `STOCK`. Un provider n'est `CONNECTED` que si son adapter réel est configuré. En V1, achats et stock locaux le sont ; PrestaShop ventes l'est avec URL, clé issue de l'environnement et états payés ; ShopCaisse demeure `NOT_CONFIGURED` hors import explicite ; Qonto demeure `NOT_CONFIGURED`.

## Jobs, planification et reprise

Les cadences viennent de `DATA_HUB_SALES_INTERVAL_SECONDS`, `DATA_HUB_BANK_INTERVAL_SECONDS` et `DATA_HUB_PROJECTION_INTERVAL_SECONDS`. Les jobs définissent dépendances, prochaine exécution et tentatives. Une mise à jour SQLite conditionnelle fournit le claim exclusif. Les erreurs explicitement `retryable` utilisent un backoff exponentiel plafonné ; configuration, authentification et validation ne sont pas rejouées agressivement. Le curseur n'avance qu'après succès.

Chaînes préparées : ventes → métriques → marketing → dashboard et banque → rapprochement → finance → dashboard. Un prédécesseur non réussi bloque la suite et rend l'état visible. Un scheduler de production peut appeler `run_due` sans introduire une seconde file de jobs.

## Freshness, santé, alertes

La fraîcheur standard est `FRESH`, `STALE`, `ERROR`, `UNAVAILABLE`, `NOT_CONFIGURED`; ses seuils sont configurables. Le cockpit `/administration` et `/api/data-hub` exposent état global `OK/DEGRADED/ERROR`, dernière réussite, erreur assainie, lignes et jobs. Ces états constituent les signaux dédupliqués par source/job ; un notifier futur doit appliquer un cooldown avant diffusion.

## Ajouter une source

1. Implémenter un port de lecture et ses fakes, sans secret dans les objets métier.
2. Enregistrer la source avec ses capacités et un seuil de fraîcheur.
3. Normaliser vers le ledger autoritaire concerné avec une clé d'idempotence.
4. Déclarer cadence et dépendances, puis tester pagination, replay, panne et recovery.
5. N'activer `configured=True` qu'après validation réelle de la configuration.

Le safe mode n'autorise aucune écriture externe. Les synchronisations n'altèrent jamais identité Catalogue, EAN, PRIMARY, stock physique, PrestaShop, ShopCaisse ou banque.
