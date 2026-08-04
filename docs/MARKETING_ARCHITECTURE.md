# Architecture Marketing

Le module étend l'architecture existante : ledgers → signaux déterministes →
opportunité → proposition → Creative AI provider-neutral → Human Review → pipeline
de publication sûr → snapshots → mesure. Il ne crée aucun second pipeline.
Les providers externes, la conformité officielle et la publication réelle restent
BLOCKED. Aucun token n'entre dans les preuves, metadata ou audits.

## Operations Cockpit (2026-08-04)

`MarketingOperationsService` est un read model au-dessus des repositories existants, pas une seconde architecture. Il fournit cockpit, calendrier, queue, review, campagnes, recherche et pagination. Tous les providers réels restent `NOT_CONFIGURED`; aucune branche de ce service ne possède un publisher.
