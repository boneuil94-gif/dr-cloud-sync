# Stockage des sauvegardes DrCloud OS

`DRCLOUD_BACKUP_DIR` est l'unique configuration du répertoire de sauvegarde. Sa
valeur par défaut est `<DRCLOUD_DATA_DIR>/backups`. En production OVH elle vaut
`/data/backups`, dans le volume Docker nommé persistant `drcloud-data`; elle ne
dépend donc ni du répertoire de travail `/app`, remplacé à chaque image, ni de
`/tmp`. Le répertoire n'est associé à aucune route HTTP ou route statique.

Le conteneur s'exécute avec l'utilisateur système non-root `drcloud` (UID/GID
attribués lors de la construction de l'image).
Au démarrage, l'application tente de créer le répertoire avec le mode `0700`,
puis vérifie lecture et écriture. Une erreur laisse le Dashboard disponible,
mais bloque toute opération exigeant une sauvegarde. La carte **Administration
> Maintenance > Sauvegardes** constitue le point de supervision opérateur.

## Déploiement et permissions

Compose monte `drcloud-data` sur `/data`; le `Dockerfile` initialise `/data` au
propriétaire non-root `drcloud`. Après déploiement, contrôler :

```sh
docker compose exec -T drcloud-os sh -c 'id && test -r /data/backups && test -w /data/backups'
curl --fail http://127.0.0.1:8080/health
```

Les bundles sont écrits dans un répertoire privé temporaire, validés (taille,
SHA-256, composants et `PRAGMA integrity_check` SQLite), puis publiés par
renommage atomique. Leur UUID empêche les collisions. Seuls les bundles complets
marqués `SUCCESS` sont détectés après redémarrage.

## Restauration opérateur (manuelle uniquement)

Arrêter l'application, copier le volume `/data` hors site, choisir l'identifiant
du bundle dans l'administration, valider `metadata.json`, les empreintes et
l'intégrité SQLite, puis utiliser la commande de restauration hors ligne
existante. Conserver la copie préalable jusqu'à validation fonctionnelle. Cette
PR n'ajoute volontairement ni téléchargement HTTP ni restauration en un clic.

Il n'existe actuellement aucune suppression automatique : la capacité disque et
l'âge de la dernière sauvegarde doivent être supervisés. Une politique de
rétention, une planification et une copie cloud chiffrée seront définies dans une
évolution future, sans modifier l'interface centrale du service.
