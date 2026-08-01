# Opérations de sécurité

## Checklist exploitation

- [ ] HTTPS valide; HSTS au proxy après validation de tous les sous-domaines.
- [ ] Secret de signature et mot de passe admin uniques, longs et tournés.
- [ ] Utilisateurs nominatifs; comptes sortants désactivés; rôles au moindre privilège.
- [ ] Références Qonto, PrestaShop et ShopCaisse actives; aucune valeur en DB/log/UI.
- [ ] `/data`, SQLite, médias, backups en `0700/0600`; `drcloud.env` en `0600`.
- [ ] Backup local contrôlé et copie chiffrée hors VPS; test de restauration périodique.
- [ ] CI en permissions minimales, dépendances surveillées, artifacts examinés.
- [ ] Sessions de l'utilisateur révoquées après incident ou retrait d'accès.
- [ ] Événements login/refus/secret/banque/backup examinés sans données sensibles.

## Utilisateur et retrait d'accès

Dans Sécurité, créer un utilisateur avec un mot de passe temporaire conforme et le rôle minimal. Pour un départ, confirmer la désactivation : elle invalide immédiatement ses sessions. Un reset mot de passe et une modification de rôles ont le même effet. Ne jamais partager l'admin principal.

## Backup, restauration et incident

La restauration reste une opération infrastructure forte : arrêter l'application, faire une copie préalable, vérifier manifeste/provenance, restaurer en `0600`, redémarrer et révoquer toutes les sessions si la base de sécurité a été restaurée. Journaliser l'opérateur et le résultat dans le registre d'incident. En cas de fuite : isoler, préserver les logs expurgés, révoquer sessions et credentials providers, tourner le secret de session, restaurer si nécessaire, puis documenter cause et contrôles.

## Réponse à incident et révocation

Désactiver immédiatement l'utilisateur concerné, révoquer ses sessions, faire tourner
les références de secrets potentiellement touchées hors application, puis conserver
et exporter le SQLite/audit en lecture seule. Un changement de mot de passe, de rôle
ou de statut invalide les sessions existantes. Les opérateurs corrèlent les événements
par `request_id`; aucune metadata brute contenant token, cookie ou Authorization ne
doit être copiée dans le ticket d'incident.
