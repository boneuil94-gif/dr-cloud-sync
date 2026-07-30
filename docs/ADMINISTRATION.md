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

## Source de vérité du déploiement

Le commit servi vient de la métadonnée de build `DRCLOUD_BUILD_COMMIT`. Le dernier
déploiement réussi vient exclusivement du fichier minimal
`last-successful-commit`, publié par `update.sh` dans un répertoire d'état dédié. Seul
ce répertoire (et non `/opt/drcloud-os`, le dépôt ou `.git`) est monté dans le
conteneur, en lecture seule. Le chemin du montage et le contenu non validé ne sont
jamais renvoyés par l'API. Un SHA doit comporter exactement 40 caractères
hexadécimaux ; fichier absent, vide, illisible ou invalide signifie **INCONNU** sans
faire échouer les autres collecteurs.

Pendant un déploiement, le nouveau conteneur voit encore le SHA précédemment validé :
la carte peut donc indiquer temporairement une divergence, mais jamais un faux OK.
Après le health local, le health HTTPS et le contrôle du SHA exact, `update.sh`
remplace atomiquement le marqueur et la carte devient **OK / Conforme**. En cas
d'échec, le marqueur n'est pas avancé. Après reconstruction et validation du rollback,
il est republié avec le SHA réellement servi ; SQLite n'est pas restaurée. Lors d'une
première installation, le répertoire existe mais aucun succès n'est inventé : la
valeur reste **INCONNU** jusqu'au premier passage complet réussi d'`update.sh`.

La vue n'offre ni restauration, ni rollback, ni déploiement, ni shell, ni monitoring
Docker, ni alerting. L'espace final de la page réserve explicitement les
futurs composants (jobs, connecteurs et synchronisations) sans simuler leurs données.
