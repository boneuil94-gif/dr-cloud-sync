# Workflow Inventaire → Stock local

## États et transitions

Une session suit uniquement `IN_PROGRESS → COMPLETED → PROPOSED → VALIDATED → APPLIED`.
`DRAFT` reste accepté pour les anciennes données mais aucune nouvelle session ne demeure en brouillon.
La clôture exige les 478 comptages. `COMPLETED` et tous les états suivants sont gelés : toute
correction requiert une nouvelle session, l'historique précédent restant intact.

La clôture crée une proposition persistée unique par session (`UNIQUE(session_id)`). Elle contient
un instantané de chaque produit DrCloud, quantité physique, quantité de référence, delta et, pour
les deltas non nuls, le mouvement et sa clé. La référence est le `stock_prestashop` du catalogue
validé chargé au début du workflow : c'est l'unique référence disponible dans l'architecture
actuelle. La quantité est figée dans la proposition et son SHA-256 permet d'identifier l'instantané.
Une future projection locale canonique devra remplacer explicitement cette source, sans recalculer
les propositions historiques.

## Validation, idempotence et transaction

La revue humaine est obligatoire. L'action UI « Valider et appliquer » réalise deux transitions
domaine distinctes. L'acteur authentifié et les dates sont conservés. Aucun connecteur distant
n'est appelé.

Les clés suivent `inventory:<session_id>:<drcloud_product_key>:v1`. L'unicité du ledger et celle de
la proposition empêchent les doublons. Tous les appels à `StockService.apply` et la transition
`APPLIED` partagent une transaction SQLite `BEGIN IMMEDIATE`. Une erreur provoque un rollback de
tous les mouvements ; la proposition reste `VALIDATED`, reçoit un message générique sans détail
technique, puis peut être rejouée. Un replay déjà appliqué retourne le résultat persistant.

## Migration SQLite

Au démarrage, les tables `inventory_stock_proposals` et `inventory_stock_proposal_lines` sont
créées avec `IF NOT EXISTS`. L'ancienne table `sessions`, dont le `CHECK` ne connaissait pas
`PROPOSED` et `APPLIED`, est reconstruite en conservant toutes ses lignes. Les comptages, exports,
historiques et mouvements existants ne sont jamais supprimés.
