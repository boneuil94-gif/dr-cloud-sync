# Social Analytics Live

## Audit avant modification

| Capacité | Existe | Alimentée | API | UI | Tests | Action restante |
|---|---:|---:|---:|---:|---:|---|
| Snapshots sociaux nullables | oui | provider de test seulement | oui | partielle | oui | cockpit live |
| Publication / connexions | oui | non | oui | oui | oui | providers homologués (bloqué) |
| Propositions / Creative AI / review | oui | locale | oui | oui | oui | signaux stock/marge |
| Sales Ledger | oui | imports réels | oui | oui | oui | mesure avant/après |
| Stock local | oui | ledger local | oui | oui | oui | moteur marketing |
| Achats, coûts, marges | oui | couverture variable | oui | oui | oui | garde-fous marketing |
| Data Hub / worker | oui | sources configurées | oui | oui | oui | jobs intelligence |
| Dashboard / Administration / Roadmap | oui | oui | oui | oui | oui | exposer la progression |
| Alertes / AuditLog | partiel | audit marketing | partiel | partielle | oui | enrichir alertes opérateur |
| Learning Loop | non | non | non | non | non | mesure prudente |

L'implémentation réutilise les tables `social_connections`, `social_post_results` et
`social_analytics_snapshots`. Le cockpit `/marketing/social-analytics` normalise les
métriques disponibles, avec période, source, fraîcheur, disponibilité, valeur et
unité. Une valeur inconnue reste `null` et s'affiche « — » / « Non disponible ».

Les états sont CONNECTED, PARTIAL, STALE, NOT_CONFIGURED, API_NOT_EXPOSED,
SCOPE_MISSING et ERROR. Instagram, Facebook, TikTok et Snapchat restent
NOT_CONFIGURED sans connexion homologuée. Aucun historique n'est synthétisé.
