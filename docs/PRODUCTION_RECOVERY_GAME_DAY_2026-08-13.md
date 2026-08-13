# Production Recovery Game Day — 13 août 2026

## Identité et verdict

Le workflow **Recovery Game Day #9**, run ID `31685249526`, a exécuté le mode `full` au SHA `35fa1fef1b8d687c4da342120f69f3f62cb502ef`.

- Restore : `PRODUCTION_DATA_PROVEN` (`PRODUCTION_DATA_RESTORE`)
- Rollback : `ROLLBACK_PROVEN` (`OVH_EQUIVALENT_ROLLBACK`)
- N : `35fa1fef1b8d687c4da342120f69f3f62cb502ef`
- N-1 : `578d70e94f835207d8c3c087ffd36ac4027c4ff2`
- Health N, N-1 et retour N : `HEALTH_OK`
- Compatibilité schéma : `COMPATIBLE`
- Contrôle de perte de données : `PASS`

La preuve assainie est persistée dans [`docs/evidence/recovery_evidence_production_2026-08-13.json`](evidence/recovery_evidence_production_2026-08-13.json). Elle ne contient ni secret, credential, token, PII, ni donnée métier individuelle.

## Restauration et intégrité

L'application a démarré (`APP_BOOT_OK`) et son healthcheck a répondu `HEALTH_OK`. Les contrôles SQLite donnent `quick_check = ok`, `integrity_check = ok` et `foreign_key_check = OK`. La copie restaurée mesure `70 877 184` octets et contient 124 tables et 223 index. Le RTO observé est **21,0 secondes**.

Les agrégats observés (`bank_transactions = 2 730`, `sumup_transactions = 10 978`, `sumup_payouts = 228`, `sales = 790`, `sale_events = 960`) prouvent uniquement la **présence** de données dans le backup restauré. Ils ne prouvent ni exhaustivité, ni coverage, ni égalité avec les totaux d'autorité Qonto, SumUp, ShopCaisse ou PrestaShop; aucun score connecteur n'en est déduit.

## RPO : mesure technique de confiance faible

Le RPO observé est `63 221,99 s`, avec confiance `LOW`, méthode `backup_created_at` et `data_max_at = null`. Il ne constitue donc **pas un RPO métier fiable** et ne prouve aucune fraîcheur à la source.

## Garde-fous

- `safe_mode = true`
- `external_provider_auth = NONE`
- `network = INTERNAL_ONLY`; `network_exposure = NONE`
- `production_database_target = false`
- `production_volume_mounted = false`
- `production_port_published = false`

## Limites restantes

Le backup valide reste `BACKUP_ON_HOST_ONLY`. Ne sont toujours pas prouvés : copie chiffrée hors VPS, restauration réellement off-host, monitoring externe/alerting, SLO opérationnel, rotation des secrets, état bootstrap/secrets et répétition périodique du DR Game Day. La concurrence/crash SQLite multi-worker reste partielle. La couverture des connecteurs, le funnel financier et les providers sociaux restent inchangés.
