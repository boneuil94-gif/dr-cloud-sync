# Production Recovery Game Day — 12 août 2026

## Verdict

**SUCCESS · `PRODUCTION_DATA_PROVEN`**

Le workflow GitHub Actions **DrCloud OS Recovery Game Day #6** a restauré un vrai backup de production dans un environnement Docker isolé, sans monter la base ni le volume de production, puis a démarré l'application et obtenu `HEALTH_OK`.

- Run ID : `31585890450`
- SHA testé : `dfe525ac4e1f7ef08357dad9bc1e3a588d9bf51a`
- Mode : `restore-only`
- Backup : `PRODUCTION_BACKUP_VALID`
- Restore : `PRODUCTION_DATA_PROVEN`
- App boot : `APP_BOOT_OK`
- Health : `HEALTH_OK`
- `quick_check` : `ok`
- `integrity_check` : `ok`
- `foreign_key_check` : `OK`
- Base restaurée : `70,647,808` octets
- Tables : `124`
- Index : `223`
- RTO observé : `19.0 s`
- RPO observé : `117.378 s`, confiance `LOW`, méthode `backup_created_at`

La preuve assainie persistée dans le dépôt est :
[`docs/evidence/recovery_evidence_production_2026-08-12.json`](evidence/recovery_evidence_production_2026-08-12.json).

## Données réellement présentes dans la copie restaurée

Les compteurs sont des agrégats issus de la base restaurée, pas des fixtures ajoutées au Game Day :

| Table / domaine | Lignes |
|---|---:|
| `bank_transactions` | 2 729 |
| `sumup_transactions` | 10 967 |
| `sumup_payouts` | 228 |
| `sales` | 790 |
| `sale_events` | 960 |
| `crm_customers` | 0 |
| `purchase_orders` | 0 |
| `stock_movements` | 0 |

Les zéros restent des zéros observés dans cette copie; ils ne sont pas transformés en preuve de complétude métier.

## Sécurité du drill

La restauration a été exécutée avec les garde-fous suivants :

- `safe_mode = true`
- auth providers externes : `NONE`
- réseau : `INTERNAL_ONLY`
- exposition réseau : `NONE`
- port production publié : `false`
- cible base production : `false`
- volume production monté : `false`
- stockage de recovery : `TEMPORARY_DOCKER_VOLUME`
- utilisateur runtime : `drcloud`

## Ce que cette preuve ferme

**P0 backup production restaurable : `CLOSED_PROVEN`.**

Le système dispose désormais d'une preuve datée qu'un bundle production complet peut être sélectionné, vérifié, restauré dans une copie isolée, puis utilisé pour démarrer DrCloud OS jusqu'au healthcheck.

Cette preuve remplace les anciennes tentatives `NOT_PROVEN` / `BACKUP_MISSING` comme dernier état de recovery connu.

## Ce que cette preuve ne ferme pas

- Rollback N → N-1 : `NOT_REQUESTED`
- Compatibilité schéma N/N-1 : `UNKNOWN`
- Backup hors hôte : non prouvé; stockage actuel `BACKUP_ON_HOST_ONLY`
- RPO métier précis : partiel, car `data_max_at` est `null` et le RPO est calculé depuis `backup_created_at`
- État secrets/bootstrap production : toujours à prouver séparément
- Concurrence SQLite multi-worker : toujours ouverte

## Politique de score

Aucun score numérique Audit V2 n'est relevé automatiquement dans cette mise à jour.

Le score global reste **58**, Production maturity **49** et Deployment **68** jusqu'à un re-score formel appliquant la méthode Audit V2. Cette décision évite de transformer une nouvelle preuve réelle en points arbitraires.

La Roadmap structurée marque en revanche immédiatement les faits nouveaux : restore production prouvé, P0 backup restaurable fermé, rollback et backup off-host toujours ouverts.

## Prochaine étape

Exécuter le workflow en mode **`full`** sur un environnement OVH-equivalent isolé afin de prouver :

1. N courant → N-1 connu-good ;
2. boot + health sur N-1 ;
3. compatibilité du schéma et absence de perte de données ;
4. retour N-1 → N ;
5. preuve assainie `ROLLBACK_PROVEN`.

Ensuite, externaliser au moins une copie de backup hors du VPS et améliorer la mesure RPO avec `data_max_at`.
