# CRM + fidélisation v1 — architecture et audit initial

## Audit réalisé avant modification

| Capacité | Existe | Alimentée | Autorité | API | UI | Tests | Action restante |
|---|---:|---:|---|---:|---:|---:|---|
| Clients ShopCaisse | Non | Non | ShopCaisse | ventes seulement | Non | connecteur ventes | API clients à confirmer; CSV documenté comme solution future |
| Clients PrestaShop | Client disponible, sans stockage CRM | Non | PrestaShop | ressources en lecture | Non | optionnels | activer ingestion paginée avec contrat réel |
| Ventes ShopCaisse / commandes PrestaShop | Oui | selon configuration | Sales Ledger | Oui | Ventes | Oui | conserver les références client lorsqu'exposées |
| Paiements | Oui | selon SumUp/Qonto | payment ledger | Oui | Settlements | Oui | ne jamais produire une vente CRM |
| Sales Ledger | Oui | selon connecteurs | **autorité CA unique** | Oui | Oui | Oui | ajouter les liens déterministes client |
| CRM / fidélité / consentements | Non | Non | sources explicites | Non | module futur | Non | fondation v1 |
| Marketing | Oui | selon catalogue | Marketing interne | Oui | Oui | Oui | consommation de segments après Human Review |
| Dashboard / Administration / Data Hub | Oui | Oui/partiel | projections | Oui | Oui | Oui | exposer états CRM honnêtes |
| AuditLog / RBAC / jobs | Oui | Oui | sécurité interne | Oui | Admin | Oui | permissions et jobs CRM |
| Roadmap | Oui | Oui | fichier roadmap | Oui | Oui | Oui | jalons CRM honnêtes |

## Autorités et limites

L'identité canonique conserve séparément les références `SHOPCAISSE`, `PRESTASHOP` et `INTERNAL`. Une vente anonyme ne crée aucun `Customer`. Le nom ou l'adresse seuls ne fusionnent jamais deux profils. L'email ou le téléphone seuls créent un candidat `POSSIBLE`; seuls email **et** téléphone exacts autorisent le rapprochement automatique. Les contradictions restent à examiner.

Les montants sont projetés par jointure avec `sale_events`; `crm_sale_links` ne copie aucune vente. Les paiements restent exclusivement dans leur ledger. Les consentements sont append-only au niveau métier et absents par défaut: l'état calculé est alors `UNKNOWN`, jamais `GRANTED`. Les drapeaux PrestaShop `newsletter` et `optin` gardent source, date, finalité et preuve de champ.

ShopCaisse n'a pas de contrat client démontré dans le dépôt: identité, adresse, fidélité et consentement sont donc `API_NOT_EXPOSED` jusqu'à preuve contraire. Aucun envoi, ni écriture ShopCaisse/PrestaShop, n'est implémenté.

## Modèle livré

Les tables couvrent clients, identités/références externes, adresses, consentements, tags, segments/memberships, liens de ventes, snapshots métriques/RFM, activités/interactions, comptes et registre de points, recommandations, campagnes internes et historique de fusion. Le registre de points est append-only; une correction est compensatoire. Le moteur fidélité démarre systématiquement en simulation.

## RFM v1

Les scores 1–5 conservent valeur, règle, période et date. Les seuils par défaut sont explicites dans le code et doivent devenir administrables avant activation métier. Les segments générés sont Champions, Fidèles, Nouveaux clients, Inactifs et Données insuffisantes. Les segments métier avancés et règles dynamiques versionnées disposent du stockage mais leur éditeur/recalcul complet reste à livrer.

## Confidentialité

Les lectures masquent email et téléphone sauf permission élevée. Toute campagne externe est impossible; la revue retourne `BLOCKED_CONSENT` dès qu'un destinataire n'est pas explicitement `GRANTED`. Fusion réversible, anonymisation contrôlée, exports protégés et écrans 360 complets restent des jalons non terminés.
