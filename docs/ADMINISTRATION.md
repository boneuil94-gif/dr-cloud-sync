# Administration et observabilité

La vue authentifiée `/administration` fournit une synthèse lisible de l'application, de
SQLite, des sauvegardes, du déploiement et du stockage. Son API
`/api/admin/status` est une projection en lecture seule construite par
`AdminStatusService`; chaque collecteur est isolé afin qu'une métrique indisponible ne
fasse pas échouer la page entière.

Les données sont volontairement limitées aux métadonnées de build non sensibles, à un
`quick_check(1)` SQLite borné, aux tailles et dates, au compte des sauvegardes, au SHA
du dernier déploiement lorsqu'il est lisible et à `shutil.disk_usage`. Aucun chemin,
secret, environnement complet, contenu de base, socket Docker ou action opérationnelle
n'est exposé. Les erreurs détaillées restent dans les journaux serveur.

Les statuts communs sont **OK**, **ATTENTION**, **ERREUR** et **INCONNU**. Le statut
global prend le niveau le plus préoccupant. Pour les sauvegardes, les seuils sont 24 h
(OK), 48 h (attention), puis erreur. Pour le disque, les seuils sont 85 % (attention)
et 95 % (erreur). Une valeur absente ou invalide devient « Inconnu » dans l'interface.
La page utilise uniquement les API Web standard côté navigateur et la bibliothèque
standard Python côté serveur.

Cette livraison n'inclut ni restauration, ni rollback, ni déploiement, ni shell, ni
monitoring Docker, ni alerting. L'espace final de la page réserve explicitement les
futurs composants (jobs, connecteurs et synchronisations) sans simuler leurs données.
