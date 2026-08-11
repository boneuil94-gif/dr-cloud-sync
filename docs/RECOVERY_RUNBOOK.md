# Recovery & rollback runbook

## Non-negotiable safety rules

**NE JAMAIS ÉCRASER L’UNIQUE COPIE D’UNE BASE INCIDENTÉE.** Preserve it read-only before restart, rollback, or restore. A restore always targets a new temporary directory. Code rollback and database restore are separate decisions; never run a destructive migration to facilitate rollback.

## Backup contract

`deploy/ovh/backup.sh` invokes `dr-cloud-sync os-backup`. The application uses SQLite's online backup API (not a hot file copy), includes `/data/media`, validates `integrity_check`, writes SHA-256 checksums, and atomically publishes a private bundle. The production default is `/data/backups` inside the `drcloud-data` Docker volume. The manifest records `backup_id`, UTC creation, basename of the source DB, size, checksum, schema fingerprint, app commit, nullable `data_max_at`, method, and status; it contains no business rows.

Frequency, rotation and retention are `UNKNOWN` unless `DRCLOUD_BACKUP_FREQUENCY`, `DRCLOUD_BACKUP_ROTATION`, and `DRCLOUD_BACKUP_RETENTION` are explicitly configured. An on-host volume is not an off-site disaster-recovery copy. Operators must verify owner/mode with `stat`; bundles should remain private (`0700` directory, `0600` database).

```bash
DRCLOUD_DATA_DIR=/data DRCLOUD_BACKUP_DIR=/data/backups dr-cloud-sync backup-status --json
```

`VALID` requires a present non-empty file, matching manifest checksum, an openable SQLite database, `quick_check=ok`, and a detectable schema. `BACKUP_INVALID` stops recovery; it is never skipped silently.

## Isolated restore drill

### GitHub Actions Production Recovery Game Day

Lancer manuellement **Actions → DrCloud OS Recovery Game Day → Run workflow**. Le seul
déclencheur est `workflow_dispatch`; le mode par défaut `restore-only` prouve le dernier
backup `VALID`, sa copie/restauration, l'intégrité, le démarrage SAFE MODE et le health
sur l'hôte OVH. Le mode `full` autorise en plus la preuve de rollback isolée. En l'absence
d'un historique ordonné de deux SHA réellement known-good, il retourne volontairement
`ROLLBACK_NOT_PROVEN` (jamais un `HEAD^` supposé).

Le job réutilise exclusivement l'identité SSH production et son `known_hosts`. Deux
`flock` interdisent un Game Day concurrent et bloquent devant un déploiement actif.
La cible est un `mktemp`, montée dans un conteneur éphémère sans credential provider,
en SAFE MODE, sur un réseau Docker interne et un port loopback aléatoire. Le volume
`drcloud-data`, la base active et les conteneurs live ne sont jamais montés comme cible,
arrêtés ou modifiés. Le trap supprime conteneur, réseau et répertoire, y compris en erreur.

Le seul artefact téléchargé est `recovery_evidence_production.json`, rétention 30 jours.
Un scan récursif refuse les clés et motifs de secrets avant copie. Il contient seulement
l'identifiant/basename du backup, des métriques structurelles anonymisées, intégrité,
RPO/RTO et résultats de rollback. `BACKUP_ON_HOST_ONLY` reste un risque explicite tant
qu'aucune preuve off-site/redundante n'existe. L'existence du workflow n'est pas une
preuve : seuls `PRODUCTION_DATA_PROVEN` et, séparément, `ROLLBACK_PROVEN` issus d'une
exécution réussie peuvent faire évoluer la matrice.

```bash
DRCLOUD_DATA_DIR=/data DRCLOUD_BACKUP_DIR=/data/backups \
DRCLOUD_RECOVERY_REPORT=/var/lib/drcloud/evidence/recovery_evidence.json \
dr-cloud-sync recovery-report --json
```

The command selects the newest `VALID` backup, copies it to a fresh `drcloud-restore-*` directory, rechecks checksum, `integrity_check`, foreign keys, and schema fingerprint, boots the real WSGI application on an ephemeral loopback port, calls public `/health`, records structural counts, shuts it down, and lets `TemporaryDirectory` remove the isolated environment. Production is never mounted writable.

Observed RPO is the interval from backup creation to the simulated incident start; because this is not necessarily the last reliable business timestamp, the method says so. `target_rpo` and `target_rto` default to `null` until business owners define them. Observed RTO is measured from drill start through successful health, with database-copy, app-boot, and health phases retained separately.

## Controlled rollback and migration policy

Run only on staging/temporary infrastructure equivalent to OVH:

1. Boot known-good SHA N-1 against a copied database; record SHA, health, schema fingerprint, and sanitized counts.
2. Deploy SHA N; require health and served SHA N.
3. Add a disposable row and apply only the real additive migration.
4. Deploy N-1 without changing the database; require health, SHA N-1, identical pre-existing counts plus the disposable row, and schema readability.
5. Save the sanitized report and run `DRCLOUD_ROLLBACK_REPORT=... dr-cloud-sync rollback-check --json`.

Policy is **additive-first**. N and N-1 must coexist for one release window. Renames/drops/type changes use expand–migrate–contract and the contract step waits until N-1 is outside the rollback window. If N-1 cannot read N's schema, declare `ROLLBACK_SCHEMA_INCOMPATIBLE`, stop automatic code rollback, keep the incident copy, and use a forward fix or explicitly approved restore. Never reverse a destructive migration during an incident.

No production or equivalent staging rollback was available during the 2026-08-10 local exercise, so rollback remains `ROLLBACK_NOT_PROVEN` and schema compatibility `UNKNOWN` (P1).

## Failure injection (isolated only)

Exercise missing/corrupt DB, checksum mismatch, read-only destination, write-failure/full-disk simulation, boot failure, persistent unhealthy endpoint, and schema drift. Every case must return `RESTORE_FAILED`, `BACKUP_INVALID`, or a named check failure—never success. The SQLite crash test starts a real WAL transaction, sends SIGKILL before commit, reopens the database, runs `integrity_check`, and proves the uncommitted row absent.

## PRODUCTION DOWN

1. Confirm and timestamp the incident; stop writers if safe.
2. Inspect the latest `VALID` backup and its age.
3. Determine the last known-good served SHA.
4. Decide restart vs code rollback vs isolated data restore.
5. Preserve and checksum the failed database **before doing anything else**.
6. Restore into a new location and retain the source.
7. Require integrity, foreign-key, schema, and structural checks.
8. Boot and require `/health` on an isolated port.
9. Verify critical read-only sources and sanitized counts/timestamps.
10. Record operation ID, actor, environment, commit, backup ID, start/end/result, sanitized error, RPO/RTO, and incident decision.

Only after approval and successful isolated validation may the restored copy replace the service database using a separately reviewed cutover procedure.
