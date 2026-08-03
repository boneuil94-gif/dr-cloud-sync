# Cockpit Settlements

## États métier

Le cockpit synthétise l'état réel sans créer de données : **READY** signifie que les quatre étapes sont alimentées, **PARTIAL** qu'une partie de la chaîne reste exploitable, **STALE** qu'une source doit être actualisée et **ERROR** qu'une erreur active empêche le calcul. Une erreur historique n'entraîne jamais `ERROR`.

Les helpers `formatCount`, `formatMoney`, `formatPercent`, `formatFreshness` et `formatStatus` centralisent l'affichage. Une valeur inconnue devient `—`, un taux non calculable devient « Non disponible » et aucune division par zéro n'est présentée.

## Vues et filtres

Les huit KPI, la frise responsive et la zone compacte « À traiter » expliquent immédiatement la couverture. Les vues Rapprochements, Transactions, Payouts et Tendances utilisent uniquement les données réelles. Recherche, statut, période et bornes de montant sont combinables ; les chips indiquent les filtres actifs. Les résultats sont paginés à 12 éléments. En l'absence de liens, une carte explique la source manquante et propose synchronisation, recalcul ou accès aux sources.

Le détail latéral présente ShopCaisse, transaction SumUp, payout, Qonto, preuves et historique sans JSON brut. Confirmer, rejeter, détacher, marquer à revoir, recalculer et ajouter une note exigent une confirmation ; l'API applique permission, CSRF et AuditLog.

## Responsive et accessibilité

Sous 1 000 px, la table devient une liste de cartes et la frise devient verticale. À 320 px, KPI et filtres passent sur une colonne ; le drawer occupe tout l'écran. Les contrôles ont des labels, les notifications utilisent `aria-live`, les statuts combinent texte et couleur, et le focus clavier est visible.

## Performance et données

Les appels récapitulatif, liens et Data Hub sont parallélisés. Le navigateur filtre une projection bornée et n'affiche que 12 lignes ; aucune donnée de démonstration n'est injectée. Les graphiques restent compacts et annoncent l'absence de séries journalières plutôt que d'afficher un canevas vide.

## Settlement Explorer

Le Cockpit propose désormais un accès direct à `/settlements/explorer`. Le Cockpit reste la synthèse de pilotage; l’Explorer est la vue d’investigation paginée, filtrable et actionnable. Les filtres sont conservés dans l’URL lors des allers-retours.

## Données alimentées

La frise affiche désormais le nombre et le montant CARD ShopCaisse, les transactions SumUp finales et leur couverture, puis les transactions réellement rattachées aux payouts. Qonto affiche « non configuré » tant qu'aucun connecteur/compte attesté n'existe : zéro n'est pas interprété comme une absence bancaire prouvée. Les KPI exposent MATCHED/POSSIBLE/UNMATCHED/CONFLICT, montants rapproché/non rapproché, taux principal par nombre et taux secondaire par montant. Si les paiements existent sans lien déterministe, l'état vide le dit explicitement et conserve les actions Recalculer et anomalies.

## Cockpit quotidien de trésorerie

Le premier niveau répond désormais à **Encaissé / En transit / Versé / À vérifier**. Les volumes de lignes sont relégués au résumé technique. Les agrégats serveur `cash_summary`, `in_transit`, `expected_payouts`, `anomaly_breakdown`, `settlement_coverage` et `daily_trends` alimentent ces vues sans scan ni calcul croisé dans le navigateur.

### Formules et autorités

* **Total encaissé** = lignes `SALE` − lignes `REFUND` du Sales Ledger pour le jour; lui seul constitue le CA.
* **CB déclaré** = paiements ShopCaisse valides canoniquement `CARD`; **CB traité** = transactions SumUp finales.
* **Frais** = frais SumUp réellement exposés. **Net attendu** = brut − frais − remboursements − chargebacks ± ajustements.
* **En transit** = net des transactions finales sans payout + payouts sans crédit bancaire rapproché. **Versé** = payouts liés à un crédit bancaire réel. **Écart** = net attendu − reçu.
* La couverture principale est calculée par montant; la couverture par nombre reste secondaire. Un dénominateur nul donne « Non disponible », jamais `NaN`.

Qonto non configuré produit une seule alerte de configuration et des projections `NOT_EVALUATED / WAITING_FOR_BANK_SOURCE`, pas une anomalie `NO_BANK_CANDIDATE` par payout. Un zéro n'est affiché que si la source est disponible et prouve réellement zéro. La projection attendue conserve la date payout exposée; elle indique « Date non disponible » quand SumUp n'en fournit aucune.

## Audit de l'existant

| Capacité | Existe | Données réelles | UI actuelle | Action restante |
|---|---:|---:|---:|---|
| Sales Ledger / CA | Oui | Oui | KPI encaissé | Surveiller fraîcheur |
| Paiements ShopCaisse + mapping CARD | Oui | Selon synchronisation | KPI CB et diagnostic explicite | Corriger les libellés inconnus à la source |
| Transactions, frais, refunds, chargebacks SumUp | Oui | Selon API | KPI, transit, tendances | Aucun |
| Payouts SumUp et composition | Oui | Oui si exposée | À recevoir / transit | Composition absente reste indisponible |
| Qonto / Bank Ledger | Oui | Conditionnel | Non configuré ou versé réel | Configurer le connecteur |
| Settlement engine / liens / preuves | Oui | Oui | Pipeline, table, drawer | Aucun nouveau ledger |
| Explorer | Oui | Oui, paginé | Lien et investigation | Aucun |
| Finance / Dashboard / Data Hub | Oui | Oui selon sources | Fraîcheur et navigation | Harmoniser les prochains runs |
| Alertes classées | Oui | Oui | Top 5 traitable | Enrichir les délais métier configurables |
| Graphiques / tendances | Partiel | Séries SumUp réelles | États vides compacts | Délais payout→banque indisponibles sans Qonto |
