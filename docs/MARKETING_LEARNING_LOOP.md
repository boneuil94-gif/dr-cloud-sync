# Marketing Learning Loop mesuré

La mesure persistante relie hypothesis, proposal, content, product, channel,
format, audience, période, métriques sociales/commerciales, baseline 30 jours,
uplift, confiance, outcome et measured_at. Le replay est idempotent.

L'attribution est UNKNOWN, NO_SIGNAL, CORRELATED ou LIKELY. Une hausse temporelle
sans lien/code tracké reste CORRELATED; elle n'est jamais présentée comme causale.
Les scores social, commercial, stock et marge restent séparés et `null` si leurs
faits manquent. Trois observations sont requises avant toute recommandation.

## Restitution opérations

Les campagnes affichent avant/après, baseline, uplift, confiance, outcome et attribution tels que persistés. Une couverture insuffisante produit `Indisponible` ou « Données insuffisantes »; un créneau optimal n'est jamais inventé.
