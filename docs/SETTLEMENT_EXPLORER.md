# Settlement Explorer v1

## Exploitation quotidienne

La page `/settlements/explorer` complète le Cockpit sans remplacer le ledger ni le moteur. Elle recherche côté serveur un paiement ShopCaisse, une transaction SumUp, un payout, un crédit Qonto, un montant, une date ou une note. La casse et les espaces sont neutralisés; les secrets et payloads bruts sont exclus de la réponse.

Les vues **Tous**, **Anomalies**, **Transactions**, **Payouts** et **Qonto** partagent pagination, tri et filtres conservés dans l’URL. La période, les bornes de montant, la devise, le statut, la confiance et la présence du ticket ou du crédit bancaire sont disponibles. Les filtres actifs deviennent des chips supprimables.

## Détail, preuves et timeline

Le détail est chargé à la demande dans un drawer desktop et plein écran mobile. Il déroule ShopCaisse → transaction SumUp → payout → Qonto, signale explicitement les composantes indisponibles, explique la méthode et les signaux du rapprochement, puis présente la timeline et les décisions humaines. Les valeurs bancaires inutiles et données carte sensibles ne sont jamais rendues.

## Actions et RBAC

`settlements.read` ouvre les vues. `settlements.review` protège confirmation, rejet, détachement et marquage à revoir; `settlements.notes` protège les notes; `settlements.backfill` protège recalcul et historique. `settlements.export` est réservé pour l’infrastructure d’export future. STAFF ne reçoit aucun droit de revue. Les mutations utilisent la session, le contrôle CSRF, les écritures idempotentes du ledger et l'AuditLog.

## Responsive et accessibilité

La table desktop devient une liste de cartes sous 1 000 px. Le panneau de détail occupe l’écran sur mobile; filtres et fraîcheur passent à deux puis une colonne à 768/375 px. Les statuts associent icône, texte, couleur et libellé ARIA. Le focus entre dans le drawer et revient à son déclencheur.
