# Kit de déploiement OVH — DrCloud OS

Ce kit prépare un futur VPS OVH Ubuntu 24.04. Il **ne déploie rien à distance**, ne modifie ni OVH ni DNS, ne crée aucun certificat et maintient `DRCLOUD_SAFE_MODE=true` et `BARCODE_SYNC_MODE=dry-run`.

## Ordre d'installation (quand le VPS sera disponible)

1. Sur le poste administrateur, créer une clé dédiée : `ssh-keygen -t ed25519 -a 100 -f ~/.ssh/drcloud_ovh`. Ne jamais transmettre ni copier ailleurs `~/.ssh/drcloud_ovh`; seule la clé `.pub` va au serveur.
2. Avec l'accès OVH initial, ajouter la clé publique à `~/.ssh/authorized_keys`, permissions `700` sur `.ssh` et `600` sur le fichier. Ouvrir **une seconde session** avec `ssh -i ~/.ssh/drcloud_ovh utilisateur@IP_DU_VPS` et conserver la première ouverte pendant le test.
3. Copier le dépôt, puis exécuter `sudo deploy/ovh/bootstrap-ubuntu.sh`. Ce script ne change pas SSH. Vérifier `sudo ufw status verbose` (y compris IPv6 si `IPV6=yes` dans `/etc/default/ufw`) : uniquement `22/tcp`, `80/tcp`, `443/tcp`; **jamais 8080**.
4. Optionnellement créer l'exploitant : `sudo adduser drcloud`, `sudo usermod -aG docker drcloud`, puis reconnecter la session pour le groupe. Copier le dépôt sous `/opt/drcloud-os` avec des droits pour `drcloud`. Ne pas supprimer/modifier l'utilisateur OVH initial avant validation complète.
5. Après plusieurs connexions par clé réussies seulement, on pourra, dans une intervention séparée avec accès de secours OVH vérifié, définir `PasswordAuthentication no` via un fichier sous `/etc/ssh/sshd_config.d/`, valider avec `sshd -t`, recharger SSH et retester. Ce kit ne l'automatise pas.
6. `cp deploy/ovh/drcloud.env.example deploy/ovh/drcloud.env`, ou `deploy/ovh/generate-secrets.sh deploy/ovh/drcloud.env`; renseigner l'administrateur, conserver le fichier en mode `600`. Les placeholders connecteurs inutilisés peuvent rester présents car aucun connecteur live n'est activé.
7. Préparer une fois l'état avec `sudo /opt/drcloud-os/deploy/ovh/prepare-deployment-state.sh`, puis lancer `deploy/ovh/deploy.sh` et `deploy/ovh/check.sh`. Cette préparation idempotente donne le répertoire au writer `drcloud-deploy:drcloud-deploy` en `0755`; l'application `drcloud` le lit seulement via le montage `:ro`. Installer le Caddyfile uniquement **après** la procédure DNS ci-dessous.

Docker Compose lie Waitress à `127.0.0.1:8080`, conserve `/data` dans `drcloud-data`, conserve aussi `/data/backups` dans ce volume persistant, privé et writable par le processus non-root et limite les logs Docker à 5 fichiers de 10 Mio. Le seul état de déploiement transmis est le répertoire minimal `.deployment-state`, monté en lecture seule ; aucun checkout Git n'est exposé. Caddy transmet les informations proxy sans dupliquer les protections HTTP de l'application.

## Initialiser exactement 478 produits

Les fichiers attendus ne sont **pas versionnés** : `dist/` est ignoré. La CLI confirme que leurs noms de sortie historiques sont `dist/mapping-prestashop-shopcaisse-final.json` et `dist/rapport-mapping-final.json`. Utiliser exclusivement les deux fichiers validés issus du processus de mapping, vérifier leur intégrité/provenance, puis les transférer temporairement (par exemple `scp`) dans `/opt/drcloud-import`; ne jamais transférer de secret avec eux.

```bash
sudo install -d -m 0700 -o drcloud -g drcloud /opt/drcloud-import
# depuis le poste admin : scp -i ~/.ssh/drcloud_ovh LES_DEUX_FICHIERS drcloud@IP_DU_VPS:/opt/drcloud-import/
cd /opt/drcloud-os/deploy/ovh
docker compose run --rm \
  -v /opt/drcloud-import:/import:ro \
  -e INVENTORY_CATALOGUE=/import/mapping-prestashop-shopcaisse-final.json \
  -e INVENTORY_MAPPING_REPORT=/import/rapport-mapping-final.json \
  drcloud-os dr-cloud-sync os-init-catalog
```

La sortie doit annoncer `"products": 478`. Rejouer **exactement la même commande** : elle doit encore annoncer 478 (idempotence), jamais 956. Supprimer ensuite les copies temporaires avec validation humaine, une fois la sauvegarde applicative créée. Cette commande travaille uniquement dans SQLite et ne contacte pas PrestaShop/ShopCaisse.

## Caddy, DNS et HTTPS futurs

Quand l'IP est connue, créer manuellement chez le gestionnaire DNS un `A` pour `os.drcloud.fr` vers l'IPv4 et un `AAAA` uniquement si une IPv6 publique correctement routée est disponible. Ne pas inventer d'adresse. Attendre et vérifier la propagation. Ensuite seulement copier `Caddyfile.example` vers `/etc/caddy/Caddyfile`, valider `sudo caddy validate --config /etc/caddy/Caddyfile` et recharger Caddy. Caddy demandera alors automatiquement HTTPS. Avant cela, laisser l'exemple inactif pour qu'aucun certificat ne soit demandé.

## Exploitation

- Sauvegarde : `deploy/ovh/backup.sh`; planifier une copie chiffrée hors VPS et tester `RESTORE.md`. La sauvegarde standard OVH ne remplace pas SQLite Online Backup.
- Mise à jour : arbre propre puis `deploy/ovh/update.sh <tag-ou-commit>`. Le script fetch, détache le commit vérifié, sauvegarde, reconstruit, contrôle `/health` et revient au commit précédent si nécessaire. Aucun `reset --hard`, volume ou DB n'est supprimé.
- Santé : `deploy/ovh/check.sh`. Après publication seulement : `DRCLOUD_HTTPS_URL=https://os.drcloud.fr deploy/ovh/check.sh`.
- Correctifs Ubuntu : `unattended-upgrades` installe les correctifs sans redémarrage automatique. Examiner `/var/run/reboot-required` et planifier toute fenêtre de redémarrage, après backup et avec contrôle health.
- Fail2ban : cinq échecs SSH sur dix minutes entraînent un ban de quinze minutes, compromis volontairement non agressif. Vérifier avec `sudo fail2ban-client status sshd`.

Toute activation EAN/stock live exige une revue séparée ultérieure. Ce kit n'effectue aucune écriture externe.
