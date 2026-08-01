# External Platforms Activation V1 — inventaire audité

Audit du dépôt au 1 août 2026. **CONNECTED** signifie qu'un health check authentifié a réussi dans le runtime concerné ; la présence d'un port, d'une variable ou d'un ancien workflow ne suffit jamais. Les valeurs de secrets ne sont ni persistées ni exposées. La production doit confirmer son propre état dans Data Hub.

| Source | Provider réel | Auth | Lecture | Écriture | Automatique | Freshness | État dépôt/runtime sans secrets |
|---|---|---|---|---|---|---|---|
| ShopCaisse catalogue | `ShopCaisseClient` API v1 | clé runtime historique | sociétés/articles/prix/stock | import catalogue historique, hors worker V1 | workflow manuel seulement | non suivie | PARTIAL |
| ShopCaisse ventes | `ShopCaisseSalesProvider` | inbox protégé, pas de secret | tickets/lignes/événements réellement exportés | aucune | oui, 10 min si inbox monté | Data Hub | PARTIAL |
| ShopCaisse paiements | aucun contrat vérifié | — | non | non | non | NOT_CONFIGURED | UNSUPPORTED |
| ShopCaisse remboursements | export CSV, si présent | inbox protégé | `REFUND`/`RETURN`/`CANCELLATION` | non | oui | Data Hub | PARTIAL |
| PrestaShop catalogue | `PrestaShopClient` | `prestashop.production` | produits/combinaisons | aucune dans ce flux | provider existant | Data Hub | PARTIAL |
| PrestaShop commandes | `PrestaShopSalesProvider` GET-only | même `secret_ref` | commandes/lignes et états explicitement configurés | aucune | oui, 10 min | Data Hub | PARTIAL |
| PrestaShop paiements | aucun endpoint dédié validé | — | champs commande seulement, non déduits | non | non | NOT_CONFIGURED | UNSUPPORTED |
| PrestaShop remboursements | politique d'états explicite | même `secret_ref` | remboursements complets; partiels non valorisés sans lignes | non | oui | Data Hub | PARTIAL |
| Qonto comptes | `QontoBankProvider` | `qonto.production` | organisation/comptes | aucune | oui, après health check | Data Hub | CONNECTED seulement au runtime |
| Qonto soldes | `QontoBankProvider` | même référence | solde/solde autorisé | aucune | oui, 30 min | Data Hub | CONNECTED seulement au runtime |
| Qonto transactions | `QontoBankProvider` | même référence | pending/booked, pages | aucune | oui, 30 min | Data Hub | CONNECTED seulement au runtime |
| Fournisseurs | aucun provider homologué trouvé | référence future | aucune source structurée attestée | aucune | non | NOT_CONFIGURED | NOT_CONFIGURED |
| Instagram/Facebook/Snapchat/TikTok | ports sociaux seulement | références opaques futures | aucune API analytics homologuée | publication fail-closed | non | NOT_CONFIGURED | NOT_CONFIGURED |

## Chaînes actives

PrestaShop et l'inbox ShopCaisse alimentent le Sales Ledger idempotent puis métriques et Marketing. Qonto alimente Bank Ledger, rapprochement, Finance et Dashboard. Les chaînes fournisseurs et social ne sont pas déclarées actives sans adapter réel. Tous les appels V1 sont en lecture ; `DRCLOUD_SAFE_MODE=true` bloque les écritures externes.

## Backfill et limites

Le backfill ventes utilise `since` et les clés `source + ticket/commande + ligne + event_type`; Qonto réinitialise son curseur paginé et conserve l'upsert par transaction. Un backfill doit être prévisualisé et borné à 90 jours (social 30 jours lorsqu'un provider existera). ShopCaisse ne documente dans ce dépôt **aucun endpoint de tickets vérifié** : l'API catalogue existante n'est pas extrapolée. Aucune promesse de données fournisseurs ou sociales n'est faite.
