# Allocation des coûts

`sale_cost_allocations` est une projection séparée du Sales Ledger. FIFO consomme les lots confirmés disponibles. Exemple: 10 × 3 EUR puis 2 × 4 EUR pour une vente de 12 donnent 38 EUR. Si seulement 5 des 8 unités sont couvertes, 3 restent sans coût et la couverture vaut 62,5 %.

Un remboursement financier ne remet jamais de stock. Un retour physique ne restaure un lot que si l'allocation d'origine est prouvée; cette restauration contrôlée reste une limite V1. Sans lien, le coût du retour est indisponible. Aucun backfill n'utilise le tarif actuel. Les frais rendus restent nullables et leur allocation est manuelle en V1.
