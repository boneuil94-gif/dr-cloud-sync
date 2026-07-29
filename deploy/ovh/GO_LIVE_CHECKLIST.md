# Checklist de mise en ligne OVH

Les cases DNS/HTTPS/déploiement restent volontairement décochées tant que les opérations réelles n'ont pas eu lieu.

## Serveur et accès

- [ ] VPS disponible et IP obtenue sans l'inventer
- [ ] Ubuntu 24.04 LTS vérifié
- [ ] Accès SSH initial fonctionnel et conservé
- [ ] Clé SSH Ed25519 publique installée; connexion par clé testée dans une seconde session
- [ ] Exploitation quotidienne via `drcloud` (ou équivalent), pas root
- [ ] Mises à jour et `unattended-upgrades` vérifiés; redémarrages planifiés
- [ ] Docker Engine officiel et Compose plugin opérationnels
- [ ] Fail2ban SSH opérationnel sans règle agressive
- [ ] UFW IPv4 et IPv6 : 22/tcp, 80/tcp, 443/tcp seulement; 8080 jamais public

## Application sûre

- [ ] Secrets uniques dans `drcloud.env` mode 600, aucun secret dans Git/logs
- [ ] `DRCLOUD_SAFE_MODE=true`
- [ ] `BARCODE_SYNC_MODE=dry-run`
- [ ] Volume `drcloud-data` monté sur `/data`
- [ ] Rotation Docker `10m` / 5 fichiers constatée
- [ ] Application construite et conteneur sain
- [ ] `/health` local réussi
- [ ] Import catalogue annonce exactement 478 au premier **et au deuxième** passage
- [ ] Login et logout testés
- [ ] Roadmap, Catalogue et Inventaire vérifiés
- [ ] Backup applicatif créé et copie externe planifiée
- [ ] Restauration testée, health/login/catalogue/inventaire validés

## Publication future

- [ ] Enregistrement `A` vers l'IPv4 réelle et `AAAA` seulement si IPv6 réelle
- [ ] DNS de `os.drcloud.fr` propagé
- [ ] Caddy activé après propagation
- [ ] HTTPS et renouvellement Caddy réellement vérifiés
- [ ] URL HTTPS health vérifiée
- [ ] Affichage mobile vérifié
- [ ] Installation PWA vérifiée (sans prétendre à un mode offline)

## Activation métier ultérieure — changement séparé

- [ ] EAN live revu, autorisé et testé séparément
- [ ] Stock live revu, autorisé et testé séparément

Ne jamais cocher « déploiement production », domaine ou HTTPS sur la seule base de ce kit préparatoire.
