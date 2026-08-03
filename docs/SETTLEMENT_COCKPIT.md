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
