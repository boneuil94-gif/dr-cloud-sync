# Social Analytics connectors

Instagram, Facebook, Snapchat et TikTok sont inventoriés séparément dans Data Hub. Le dépôt possède des ports de connexion/publication et un stockage analytics, mais aucun adapter analytics API homologué avec credential réel. Les quatre sources restent **NOT_CONFIGURED**; aucune impression, reach, vue, clic ou engagement n'est inventé.

Un futur provider doit annoncer ses capabilities et ne persister que les métriques retournées, avec compte, période, identifiant externe et clé idempotente. Authentification par `secret_ref` serveur uniquement; cadence `SOCIAL_ANALYTICS_INTERVAL_SECONDS` (6 h par défaut, backfill 30 jours). Analytics et publication sont indépendants. La publication réelle reste fail-closed tant que provider et conformité ne sont pas validés.
