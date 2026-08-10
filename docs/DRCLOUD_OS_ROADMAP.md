# Roadmap DrCloud OS — contrat V3

La source d’autorité unique et machine-readable est [`config/roadmap_v3.json`](../config/roadmap_v3.json). `RoadmapService` la valide puis l’expose sans recalcul des scores à l’UI Roadmap, au Dashboard et à `GET /api/roadmap`. `MODULE_SCORECARD.md` et `ROADMAP_V3.md` sont les vues documentaires et la méthode associées à cette même coupe d’audit.

## Pourquoi la roadmap V2 a été remplacée

L’ancien manifeste `docs/drcloud-os-roadmap.json` attribuait un crédit binaire aux jalons `DONE` et un crédit fractionnaire aux sous-étapes `IN_PROGRESS`, puis pondérait les modules. Cette formule a produit **75,93 %** (118 jalons déclarés terminés sur 159 dans le Grand Audit du 4 août) et a notamment affiché Purchases à 100 %, Finance à 81,82 % et Stock à 90 %. Elle mesurait surtout la livraison de code, pas la maturité prouvée en production.

La V3 publie le **score global strict 58/100** issu du Grand Audit V2. Ce score n’est jamais la moyenne des cartes. Une hausse exige de nouvelles preuves suivant la méthode Audit V2 : code, wiring, tests, données production, observabilité et documentation/runbook. Le code seul conserve un niveau de preuve explicite et ne devient jamais un faux `DONE`.

## Mise à jour

1. Mettre à jour `config/roadmap_v3.json` uniquement lorsqu’une preuve auditable existe.
2. Reporter la même coupe dans `MODULE_SCORECARD.md` et, si les priorités changent, dans `ROADMAP_V3.md`.
3. Valider `pytest tests/test_roadmap.py` puis la suite complète.
4. Ne jamais réintroduire un calcul pondéré par jalons dans l’API ou le frontend.
