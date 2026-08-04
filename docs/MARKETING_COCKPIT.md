# Cockpit Marketing

`/marketing` est le poste de décision provider-neutral. Son en-tête expose période, fraîcheur, dernière synchronisation, revue, campagnes, mesures et l'état honnête `NOT_CONFIGURED` des providers. Huit KPI au maximum utilisent `Indisponible`, jamais un faux zéro.

## Audit de réutilisation

| Capacité | Existe | Alimentée | API | UI | Tests | Action restante |
|---|---:|---:|---:|---:|---:|---|
| Modèles, propositions, campagnes, contenus | Oui | Partielle | Oui | Oui | Oui | Données terrain |
| Creative AI / Human Review | Oui | Oui | Oui | Oui | Oui | Conformité officielle |
| Publishing Pipeline | Oui | Interne | Oui | Oui | Oui | Provider homologué |
| Social Analytics / Learning Loop | Oui | Partielle | Oui | Oui | Oui | Couverture live |
| Sales / Stock / Margin Intelligence | Oui | Selon ledgers | Oui | Oui | Oui | Fraîcheur terrain |
| Dashboard / Data Hub / jobs / alertes | Oui | Oui | Oui | Oui | Oui | Notifications externes |
| AuditLog / RBAC | Oui | Oui | Oui | Administration | Oui | Revue continue |
| Recherche / filtres / pagination | Oui | Oui | Oui | Oui | Oui | Index FTS si volumétrie |
| Export contrôlé | Infrastructure existante | Non | Non | Non | N/A | Différé : schéma métier à valider |

Les recommandations montrent signaux, données disponibles et manquantes, règle, composantes, confiance et limites. Le centre de notifications déduplique revue, blocage, provider absent et opportunité prioritaire.
