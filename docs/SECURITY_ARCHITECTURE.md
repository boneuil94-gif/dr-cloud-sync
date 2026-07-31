# Architecture de sécurité V1

## Modèle de menace

DrCloud OS protège en priorité les données banque/finance, ventes, catalogue/stock, fournisseurs, marketing et les références de credentials. Les menaces retenues sont le vol de session, le credential stuffing, l'escalade de privilèges/IDOR, les mutations CSRF, la fuite par logs/backups et l'accès au SQLite ou aux médias. L'attaquant peut connaître les URLs et modifier tout identifiant; l'autorisation est donc vérifiée avant le dispatch, et une route inconnue est refusée.

## Contrôles

L'identité durable, les rôles, permissions, sessions révocables, références de secrets, paramètres et AuditLog append-only sont dans SQLite. Le bootstrap migre additivement le credential administrateur historique et lui attribue `ADMIN`; aucun compte faible n'est créé. Les cookies sont signés, HttpOnly, SameSite=Lax, Secure en production, expirent après huit heures et portent version + identifiant de session. Désactivation, changement de rôle ou mot de passe incrémentent la version et révoquent les sessions.

Toutes les mutations authentifiées requièrent le jeton CSRF. CSP, anti-frame, nosniff, Referrer-Policy et Permissions-Policy limitent le navigateur; HSTS appartient au reverse proxy HTTPS. SQLite n'est **pas chiffré au repos** : permissions Unix, volume privé, sauvegardes chiffrées hors hôte et contrôle d'accès infrastructure sont obligatoires.

## Audit et incidents

Les connexions, refus, changements d'accès, révocations et mutations sensibles produisent des entrées uniquement ajoutées avec acteur, cible, request ID, IP, résultat et métadonnées récursivement expurgées. L'application n'expose aucune route update/delete d'audit. Une rafale de refus ou login failures est un événement à examiner; le login limite chaque IP à cinq échecs par cinq minutes sans verrou permanent exploitable.
