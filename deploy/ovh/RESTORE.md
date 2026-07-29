# Restauration contrôlée

Ne jamais restaurer sur l'application active. Choisir un backup `drcloud-os-backup-*` contenant `drcloud.db` et `metadata.json`, vérifier sa provenance, ses permissions et disposer d'une fenêtre d'intervention.

1. `cd /opt/drcloud-os/deploy/ovh` puis `docker compose stop drcloud-os`.
2. Horodater et copier la DB actuelle **sans la supprimer** :
   ```bash
   stamp=$(date -u +%Y%m%dT%H%M%SZ)
   docker run --rm -v drcloud-data:/data -v /var/backups/drcloud:/backups alpine \
     cp -p /data/drcloud.db /backups/drcloud.db.before-restore.$stamp
   ```
3. Examiner le `metadata.json`; sélectionner explicitement le répertoire. Restaurer avec un conteneur temporaire et remettre l'identité de l'image (`drcloud`, UID/GID constatés, et non supposés) :
   ```bash
   BACKUP=drcloud-os-backup-YYYYMMDDTHHMMSSZ
   uid=$(docker compose run --rm --entrypoint id drcloud-os -u)
   gid=$(docker compose run --rm --entrypoint id drcloud-os -g)
   docker run --rm -v drcloud-data:/data -v /var/backups/drcloud:/backups alpine \
     sh -c "cp /backups/$BACKUP/drcloud.db /data/drcloud.db && chown $uid:$gid /data/drcloud.db && chmod 600 /data/drcloud.db"
   ```
4. `docker compose start drcloud-os`, attendre puis exécuter `./check.sh` et `curl --fail http://127.0.0.1:8080/health`.
5. Via HTTPS une fois réellement disponible, tester le login administrateur, Catalogue (**exactement 478**), Roadmap et Inventaire (session, quantités et persistance). Vérifier aussi qu'un second `os-init-catalog` reste à 478 si l'import doit être revalidé.
6. Conserver la copie pré-restauration jusqu'à validation métier. En cas d'échec, arrêter de nouveau et restaurer cette copie par la même procédure; ne supprimer ni volume ni DB.
