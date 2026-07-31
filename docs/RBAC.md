# RBAC DrCloud OS

L'autorisation centrale applique un refus par défaut. Les routes ne comparent jamais un nom utilisateur. Safe mode et RBAC sont indépendants.

| Domaine | ADMIN | MANAGER | STAFF | READ_ONLY |
|---|---|---|---|---|
| Catalogue | lecture/écriture | lecture/écriture | lecture | lecture |
| Stock | tout, validation | tout, validation | lecture/écriture | lecture |
| Ventes | lecture/sync/mapping | lecture/sync/mapping | lecture | lecture |
| Banque / finance | lecture + sync | lecture finance/banque | aucun | aucun |
| Achats | lecture/écriture | lecture/écriture | lecture | lecture |
| Marketing | tout | tout | aucun | lecture |
| Administration/settings/backups | tout | lecture admin | aucun | aucun |
| Sécurité/utilisateurs/rôles/secrets | tout | lecture sécurité seulement | aucun | aucun |

Les permissions canoniques sont définies dans `security.PERMISSIONS`; les rôles les agrègent via les tables `security_role_permissions`. Toute modification de rôle invalide toutes les sessions de l'utilisateur. Ajouter une route exige de la déclarer dans `_route_permission` et d'ajouter des tests allow/deny; sinon `__default_deny__` la refuse.
