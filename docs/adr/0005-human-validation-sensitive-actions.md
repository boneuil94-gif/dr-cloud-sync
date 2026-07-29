# ADR 0005 — Validation humaine des actions sensibles

- Statut : accepté
- Date : 2026-07-29

## Contexte
Stock, achats, synchronisations, publications et actions proposées par IA peuvent avoir des conséquences commerciales ou irréversibles. Une automatisation correcte techniquement peut rester inappropriée métier.

## Décision
Les workflows sensibles produisent d'abord une proposition persistée. Une permission `VALIDATE` distincte autorise le passage à l'exécution, qui est auditée et idempotente. L'assistant sépare READ, PROPOSE et EXECUTE et appelle les mêmes services métier que l'interface.

## Conséquences
Les effets restent contrôlables et explicables, au prix d'une étape utilisateur et d'états supplémentaires. Les règles à faible risque pourront être pré-approuvées plus tard, explicitement et avec audit.
