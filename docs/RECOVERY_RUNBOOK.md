# Recovery Runbook

## Backup inventory

`dr-cloud-sync backup-status` inspecte les médias réellement présents (emplacement, date, taille, SHA-256) et affiche `BACKUP_PROVEN`, `BACKUP_STALE`, `BACKUP_MISSING` ou `UNKNOWN`. Fréquence, rotation et rétention restent `UNKNOWN` sans configuration explicite; un script seul ne prouve rien.

## Restore isolé

Exécuter `DRCLOUD_DATA_DIR=/data DRCLOUD_BACKUP_DIR=/backups dr-cloud-sync restore-test`. La commande choisit le dernier bundle, copie SQLite dans un répertoire temporaire, ouvre la copie, lance `integrity_check`, `foreign_key_check`, compte les tables/index et détruit le temporaire. Elle ne modifie jamais production. `app_boot` et `health_result` attestent la capacité structurelle de la copie; un game-day avec processus HTTP reste requis pour une preuve bout-en-bout.

Le rapport contient début/fin/durée, âge backup, intégrité, boot/health, `observed_rpo` (âge de la sauvegarde au début de l'incident simulé) et `observed_rto` (durée jusqu'au contrôle sain). `target_rpo` et `target_rto` restent `null` jusqu'à décision métier. Sans exécution : `RESTORE_NOT_PROVEN`.

## Rollback

Sur staging isolé : déployer un SHA connu, vérifier `/health` et SHA, relever schéma/comptages, déployer le SHA précédent, répéter health/boot/schéma/comptages, puis écrire un rapport assaini. `DRCLOUD_ROLLBACK_REPORT=… dr-cloud-sync rollback-check` ne crédite qu'un rapport réel; sinon `ROLLBACK_NOT_PROVEN`. Aucun rollback réel n'a été exécuté dans cette tâche faute d'accès staging.
