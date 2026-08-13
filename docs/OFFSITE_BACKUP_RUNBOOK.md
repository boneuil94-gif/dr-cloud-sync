# Backup offsite chiffré DrCloud OS

## Portée et architecture

La chaîne est **backup applicatif APP_RESTORABLE → validation locale → Restic → S3 compatible → snapshot distant → `restic check`**. `dr-cloud-sync os-backup` et `BackupService` restent la source de vérité; l'offsite n'est qu'une couche postérieure. Restic chiffre et authentifie les blocs côté client avant leur transfert. Aucun tar, SQLite ou bundle en clair n'est envoyé directement au fournisseur.

Le Game Day séparé choisit le dernier snapshot marqué `drcloud-os`, restaure dans un répertoire temporaire vide, valide manifeste, JSON, sommes SHA-256 et SQLite, puis seed un volume Docker jetable. Il démarre l'image de production en safe mode, sur un réseau `--internal`, sans port publié, avec rootfs read-only, capabilities supprimées et `no-new-privileges`. Le volume de production et les backups locaux ne sont jamais des sources de restauration.

## Modèle de menace et limites

Cette conception protège la confidentialité contre le fournisseur objet et rend détectables les altérations par Restic. Elle ne protège pas un job GitHub ou un hôte compromis pendant que les données et le matériel d'accès sont en mémoire. Utiliser un compte objet dédié, en écriture limitée au bucket, les protections d'immutabilité/versioning du fournisseur et des GitHub Environment reviewers. Une exécution planifiée prouve seulement que l'automatisation a tourné: seule une evidence issue d'un Game Day production réussi classe la restauration `OFFSITE_RESTORE_PROVEN`.

Le blocker `BACKUP_ON_HOST_ONLY` et les scores restent inchangés jusqu'à l'exécution réelle des deux workflows. Les tests avec doubles ne constituent jamais une preuve production.

## Configuration GitHub Environment `production`

Variables non sensibles:

* `DRCLOUD_RESTIC_IMAGE`: référence Restic obligatoire sous la forme `registry/image@sha256:<64-hex>`. Vérifier le digest publié par le mainteneur/registry; ne jamais substituer un tag mutable.
* `OFFSITE_RESTIC_REPOSITORY`: URL Restic S3, par exemple une URL factice de forme `s3:https://object.example.invalid/bucket/prefix`.
* `OFFSITE_S3_REGION` et, si requis, `OFFSITE_S3_ENDPOINT`.

Secrets:

* `OFFSITE_RESTIC_PASSWORD`;
* `OFFSITE_S3_ACCESS_KEY_ID`;
* `OFFSITE_S3_SECRET_ACCESS_KEY`;
* les secrets SSH existants `OVH_SSH_PRIVATE_KEY`, `OVH_SSH_HOST`, `OVH_SSH_KNOWN_HOSTS`, `OVH_SSH_PORT`, `OVH_SSH_USER`.

Ne jamais ajouter ces valeurs à `drcloud.env`, à la base, à une image ou au repository. Les workflows créent un fichier mode `0600`, le transfèrent dans `/tmp`, l'importent uniquement dans le processus distant, puis garantissent sa suppression (`shred -u` lorsque disponible, sinon `rm`) avec un trap et vérifient son absence. Restic s'exécute dans un conteneur distinct; aucune valeur offsite n'entre dans `drcloud-os`.

## Initialisation et exploitation

1. Créer un bucket privé S3-compatible, activer versioning/immutabilité selon le fournisseur et un principal dédié au préfixe Restic.
2. Renseigner variables et secrets dans l'Environment protégé.
3. Lancer manuellement **DrCloud OS encrypted offsite backup**. Le premier lancement initialise uniquement un repository réellement absent; authentification, réseau et corruption échouent fermés.
4. Vérifier l'artifact `offsite_backup_status.json`: `last_result=OFFSITE_REMOTE_CHECK_PROVEN` et `remote_check=PROVEN`.
5. Lancer **DrCloud OS remote-only recovery Game Day**, puis contrôler l'artifact `offsite_recovery_evidence_production.json`.

Le backup est planifié chaque lundi à 02:17 UTC dans le YAML. Modifier cette cadence uniquement par PR revue selon RPO attendu, coût et capacité. GitHub Schedule peut être retardé ou désactivé et n'est pas une preuve DR.

### Retention

Aucune suppression n'a lieu par défaut (`RETENTION_NOT_CONFIGURED`). Pour l'activer, définir une ou plusieurs variables de processus `OFFSITE_RESTIC_KEEP_DAILY`, `OFFSITE_RESTIC_KEEP_WEEKLY`, `OFFSITE_RESTIC_KEEP_MONTHLY` avec un entier entre 0 et 10000. Valider la politique avec sécurité/juridique avant configuration. Le script lance alors `forget --prune`; une erreur échoue fermée.

## Rotation

Créer d'abord un nouveau principal objet, remplacer les secrets GitHub, exécuter backup et Game Day, puis révoquer l'ancien principal. La rotation du mot de passe Restic se fait avec la commande officielle `restic key` depuis une station de secours contrôlée; conserver au moins une clé fonctionnelle jusqu'à validation. Ne jamais placer les anciennes ou nouvelles valeurs dans les logs/artifacts.

## Restauration manuelle et perte complète du VPS

Sur une machine de secours saine équipée de Docker:

1. fournir temporairement les six paramètres à un processus Restic utilisant exactement l'image épinglée;
2. exécuter `snapshots`, `check`, puis `restore <snapshot> --target <répertoire-vide>`;
3. vérifier `metadata.json`, toutes les sommes, les JSON et les trois PRAGMA SQLite;
4. suivre la procédure de restauration applicative sans monter simultanément le volume de production;
5. détruire le répertoire et effacer l'environnement temporaire après validation.

La perte totale du VPS n'affecte pas le repository objet: reconstruire l'hôte depuis le code revu, récupérer l'accès depuis GitHub Environment/escrow, puis suivre ces étapes. La perte du mot de passe Restic sans autre clé/escrow rend les données cryptographiquement irrécupérables; le fournisseur ne peut pas le réinitialiser. Maintenir une copie escrow hors ligne testée selon la politique de l'organisation.

## Evidence et observabilité

`offsite_backup_status.json` expose seulement: configuration, dernier essai/succès/snapshot/résultat, classification, chiffrement, contrôle distant, retention et preuve de restauration. Une inconnue vaut `null` ou `UNKNOWN`, jamais « healthy ».

L'evidence Game Day ne contient que des faits assainis. Son RTO est une durée observée. Si `data_max_at` manque, le RPO utilise `created_at` comme proxy avec `rpo_confidence=LOW`; ce n'est pas une fraîcheur métier fiable. Une preuve complète exige simultanément `OFFSITE_UPLOAD_PROVEN`, contrôle distant prouvé, `OFFSITE_RESTORE_PROVEN`, `APP_BOOT_OK` et `HEALTH_OK` lors d'une vraie exécution production.
