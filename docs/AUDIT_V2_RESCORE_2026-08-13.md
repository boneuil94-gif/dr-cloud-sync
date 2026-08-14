# Audit V2 — re-score formel du 13 août 2026

## Baseline et nouvelles preuves

La baseline est le Grand Audit V2 du 10 août : global strict 58, Production maturity 49 après l'addendum public, Deployment 68, Observability 61 et Test quality 76. Sa méthode impose : **20 % code/architecture, 20 % wiring, 20 % tests, 25 % preuve production, 10 % observabilité, 5 % documentation**.

Le Game Day #6 du 12 août (run `31585890450`) a apporté `PRODUCTION_DATA_RESTORE`, un RTO mesuré et un backup `BACKUP_ON_HOST_ONLY`, sans rollback. Le Game Day #9 (run `31685249526`, SHA `35fa1fef1b8d687c4da342120f69f3f62cb502ef`) apporte en plus `OVH_EQUIVALENT_ROLLBACK`, N → N-1 → N en health, schéma `COMPATIBLE` et data loss `PASS`. Il ne transforme pas `BACKUP_ON_HOST_ONLY` en preuve off-host.

## Scores before / after

| Score | Avant | Après | Décision |
|---|---:|---:|---|
| Global strict | 58 | 58 | agrégation non reproductible |
| Deployment | 68 | 79 | re-score complet des six axes |
| Production maturity | 49 | 49 | inchangé volontairement |
| Observability | 61 | 61 | inchangé volontairement |
| Test quality | 76 | 76 | inchangé volontairement |

### Deployment — 79/100

| Axe | Crédit | Preuve / limite |
|---|---:|---|
| Code / architecture | 17/20 | scripts backup/restore, déploiement au SHA et rollback structurés; SQLite/migrations distribuées restent une limite |
| Wiring | 18/20 | CI/CD OVH, health et workflow full atteignables; off-host/alerting non câblés de façon prouvée |
| Tests | 17/20 | tests statiques et recovery, puis exercice full réel; pas de charge, multi-worker ni périodicité démontrée |
| Preuve production | 19/25 | restore production-data et rollback OVH-equivalent datés; aucune restauration off-host, rotation secrets ou SLO |
| Observabilité | 4/10 | health, intégrité, RTO et RPO technique capturés; pas de monitoring externe/alerting/SLO et RPO métier non fiable |
| Documentation | 4/5 | runbooks et preuves #6/#9 assainies; cadence DR/ownership opérationnel incomplets |
| **TOTAL** | **79/100** | **68 → 79** |

Le delta vient uniquement des éléments reproductibles et persistés. Il n'a pas été choisi à l'avance.

## Dimensions volontairement inchangées

- **Production maturity 49 → 49** : le delta factuel est un restore et un rollback réels, supérieur aux tests locaux. Cependant la baseline ne fournit pas de sous-barème reproductible de cette dimension transverse; la relever imposerait un nombre arbitraire. L'offsite DR, secrets, répétition et preuves métier restent absents.
- **Observability 61 → 61** : RTO et diagnostics de recovery sont désormais observés, mais aucun monitoring externe, alerting ou SLO n'est démontré. Sans barème fin persisté, pas de delta numérique inventé.
- **Test quality 76 → 76** : le workflow full ajoute une validation production-like réelle, mais ne remplace ni E2E navigateur, ni load/concurrency testing, ni fault injection provider. La répétition périodique n'est pas encore prouvée.
- **Qonto, SumUp, ShopCaisse, PrestaShop, Finance et Settlements** restent inchangés : les volumes restaurés prouvent la présence, jamais l'exhaustivité par rapport aux sources d'autorité.

## Blockers

Fermés : backup production restaurable; rollback N → N-1 → N; compatibilité de schéma auparavant `UNKNOWN`; data loss check; mesure du RTO.

Restants : `BACKUP_ON_HOST_ONLY`, secrets/bootstrap, monitoring externe/alerting, SLO, rotation secrets, répétition DR, couverture connecteurs, funnel financier, concurrence SQLite et providers sociaux officiels.

## Politique globale et règle anti-surcrédit

L'Audit V2 dit que le global n'est pas une moyenne naïve et qu'il « privilégie » certaines preuves, mais ne persiste ni coefficients ni algorithme d'agrégation global suffisamment précis. Décision : **`GLOBAL_SCORE_UNCHANGED_NO_REPRODUCIBLE_AGGREGATION`**. Global 58 avant, 58 après.

Un statut GitHub `SUCCESS` n'accorde jamais de points par lui-même. Le crédit exige des résultats datés, assainis et vérifiables rattachés à l'axe concerné. Les trois niveaux doivent rester distincts : `PRODUCTION_DATA_RESTORE` prouve la restauration de la copie; `OVH_EQUIVALENT_ROLLBACK` prouve le cycle de versions isolé; `BACKUP_ON_HOST_ONLY` interdit toute affirmation d'offsite DR.

## Addendum formel — validation offsite réelle

Deux exécutions au SHA `2abb562469f40a9c20c8eeb875f86c1b06e83489` complètent la baseline :

- `DrCloud OS encrypted offsite backup` #2, run `31716317021`, `SUCCESS` : chiffrement client Restic, stockage objet off-host, upload et contrôle distant prouvés;
- `DrCloud OS remote-only recovery Game Day` #1, run `31718824913`, `SUCCESS` : snapshot distant disponible et intègre, restauration sans backup local, boot/health et contrôles SQLite réussis en 13 s.

La preuve structurée assainie est persistée dans `docs/evidence/offsite_dr_evidence_production_2026-08-13.json`. Elle ne contient ni secret, ni URL de repository, ni matériel cloud.

### Re-score Deployment après preuve offsite

| Axe | Avant | Après | Justification du seul delta |
|---|---:|---:|---|
| Code / architecture | 17/20 | 17/20 | aucun nouveau crédit de code par une exécution production |
| Wiring | 18/20 | 19/20 | la chaîne stockage objet → contrôle distant → restore remote-only est exercée |
| Tests | 17/20 | 17/20 | un succès production ne remplace pas les tests de charge, concurrence ou répétition |
| Preuve production | 19/25 | 23/25 | upload chiffré, présence distante, intégrité et restauration off-host sont prouvés |
| Observabilité | 4/10 | 4/10 | monitoring externe, alerting et SLO restent absents |
| Documentation | 4/5 | 4/5 | preuve ajoutée, mais ownership et cadence DR restent incomplets |
| **TOTAL** | **79/100** | **84/100** | **+5 strictement rattachés aux nouvelles preuves** |

`BACKUP_ON_HOST_ONLY` est fermé avec le statut **`PRODUCTION_PROVEN`**. Restent ouverts : fraîcheur RPO business de confiance `LOW` (12 829,331 s sur le proxy `backup_created_at`), monitoring externe/alerting, SLO, rotation des secrets et répétition périodique du DR. Le DR n'est donc déclaré ni parfait, ni complet.

Le global reste **58** avec `GLOBAL_SCORE_UNCHANGED_NO_REPRODUCIBLE_AGGREGATION`; Production maturity **49**, Observability **61** et Test quality **76** restent également inchangés. Modifier ces dimensions sans sous-barème reproductible serait inventer des points.
