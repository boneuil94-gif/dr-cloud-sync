# CI/CD de DrCloud OS

## Flux normal

Une branche de développement ouvre une pull request vers `main`. La workflow **DrCloud OS CI** installe le paquet, exécute les tests Python, valide le JavaScript, les scripts shell et Docker Compose, puis construit l'image et contrôle que la roadmap est embarquée. Une pull request ne possède aucun chemin de déploiement.

Après merge, le `push` sur `main` repasse la même CI. La workflow **DrCloud OS Production** n'est déclenchée que par la conclusion réussie de cette CI et déploie son SHA exact dans l'environment GitHub `production`. Le verrou GitHub et `flock` côté VPS empêchent deux déploiements simultanés. Le VPS sauvegarde d'abord, vérifie que le SHA appartient à `origin/main`, construit/recrée le service, puis contrôle `/health` en local et sur `https://osdrcloud.fr`, y compris le SHA exposé.

## Secrets GitHub et environment

Créer l'environment `production` (avec approbation obligatoire si souhaitée) et les secrets suivants. Aucun identifiant DrCloud applicatif ne doit être ajouté à GitHub : il reste exclusivement dans `/opt/drcloud-os/deploy/ovh/drcloud.env`.

- `OVH_SSH_HOST` : nom DNS ou IP du VPS ;
- `OVH_SSH_PORT` : port SSH (généralement `22`) ;
- `OVH_SSH_USER` : compte Unix dédié, par exemple `drcloud-deploy` ;
- `OVH_SSH_PRIVATE_KEY` : clé privée dédiée sans réutilisation ;
- `OVH_SSH_KNOWN_HOSTS` : ligne de clé hôte vérifiée hors bande (`ssh-keyscan` seul ne constitue pas une vérification).

## Installation unique sur le VPS

Avant toute modification, identifier les trois patches historiques sans les perdre :

```bash
cd /opt/drcloud-os
git status --short
git diff -- Dockerfile src/dr_cloud_sync/roadmap.py src/dr_cloud_sync/inventory.py | tee /root/drcloud-vps-local-patches.diff
```

Comparer cette archive à la PR : la roadmap doit être copiée dans `/app/docs`, `DRCLOUD_ROADMAP` doit viser ce fichier et aucun correctif d'inventaire non revu ne doit être conservé. Archiver le diff hors du checkout avant de nettoyer. Ces commandes sont indispensables, car le contenu des modifications locales du VPS n'est pas accessible depuis le dépôt.

En administrateur, après validation du diff et merge de la PR :

```bash
sudo adduser --disabled-password --gecos '' drcloud-deploy
sudo usermod -aG docker drcloud-deploy
sudo install -d -o drcloud-deploy -g drcloud-deploy -m 0750 /opt/drcloud-os
sudo chown -R drcloud-deploy:drcloud-deploy /opt/drcloud-os
sudo install -d -o drcloud-deploy -g drcloud-deploy -m 0750 /var/backups/drcloud
sudo -u drcloud-deploy git -C /opt/drcloud-os fetch origin main
sudo -u drcloud-deploy git -C /opt/drcloud-os checkout --detach origin/main
sudo install -d -o drcloud-deploy -g drcloud-deploy -m 0700 /home/drcloud-deploy/.ssh
sudoedit /home/drcloud-deploy/.ssh/authorized_keys
sudo chown drcloud-deploy:drcloud-deploy /home/drcloud-deploy/.ssh/authorized_keys
sudo chmod 0600 /home/drcloud-deploy/.ssh/authorized_keys
sudo /opt/drcloud-os/deploy/ovh/prepare-deployment-state.sh
```

Conserver le `drcloud.env` de production (mode `0600`) et y ajouter `DRCLOUD_ROADMAP=/app/docs/drcloud-os-roadmap.json`. La clé publique dédiée peut être limitée dans `authorized_keys` par IP source si les plages GitHub utilisées sont maintenues ; le compte n'a besoin d'aucun `sudo`, seulement de l'accès au dépôt, à Docker et au répertoire de sauvegarde. Le script de préparation est une opération root unique et idempotente : il fixe `.deployment-state` à `drcloud-deploy:drcloud-deploy 0755`. Les déploiements suivants n'utilisent jamais sudo. Tester ensuite manuellement une fois `update.sh <SHA-main>`.

## Échec, rollback et secours

`update.sh` sauvegarde **avant** le checkout. Si le nouveau service ou l'un des deux health checks échoue, il revient au SHA précédent, reconstruit, puis contrôle le rollback. Il ne restaure jamais automatiquement SQLite : restauration de code et restauration de données sont volontairement distinctes pour éviter toute perte d'écritures. La sortie Actions fournit les SHA, états Compose et 100 dernières lignes applicatives.

Rollback manuel d'urgence :

```bash
sudo -u drcloud-deploy /opt/drcloud-os/deploy/ovh/update.sh <SHA_MAIN_CONNU_FONCTIONNEL>
```

Une restauration de données ne doit être entreprise qu'après arrêt des écritures et selon `deploy/ovh/RESTORE.md`. Pour suspendre temporairement l'automatisation, désactiver la workflow **DrCloud OS Production** dans GitHub Actions ou ajouter une règle d'approbation à l'environment `production`; ne pas modifier le VPS.

## Fondations d'observabilité

Le health public ne contient aucun secret et expose `status`, l'état SQLite, la version du paquet, le commit et la date de build. Ces données constituent le socle d'une future vue Administration. L'espace disque et l'état du conteneur sont déjà contrôlés par `check.sh`; dernière sauvegarde, jobs, synchronisations, connecteurs et erreurs récentes devront être ajoutés derrière une route authentifiée, sans transformer `/health` en système de monitoring.

## Vue Administration authentifiée

`/administration` est le centre de supervision interne, alimenté en lecture seule par
`/api/admin/status`. Ces deux routes exigent la session DrCloud OS. L'API expose une
liste fermée d'informations non sensibles : version, commit et build servis, contrôle
SQLite léger (`quick_check(1)`), taille de la base, inventaire et ancienneté des
sauvegardes, cohérence avec `.last-successful-commit`, et occupation du disque obtenue
avec `shutil.disk_usage`. Elle ne retourne ni chemins internes, ni configuration, ni
variables d'environnement, ni secrets, et ne donne accès à aucune action de
restauration, rollback, déploiement ou commande système.

Les statuts sont `ok`, `warning`, `error` et `unknown`. Une sauvegarde de 24 heures au
plus est OK, de 24 à 48 heures déclenche une attention, et au-delà une erreur ; un
répertoire inaccessible reste inconnu tandis qu'un répertoire accessible sans
sauvegarde déclenche une attention. Le disque passe en attention à 85 % et en erreur à
95 %. Une collecte défaillante est journalisée côté serveur et dégrade uniquement sa
section. `/health` reste public, minimal et inchangé pour la CI/CD.

Le conteneur lit les sauvegardes via le montage `/backups` existant. Aucun socket Docker
ni privilège supplémentaire n'est utilisé.

### Publication atomique du dernier succès

`update.sh` détermine une fois le chemin canonique absolu du petit répertoire d'état
(`deploy/ovh/.deployment-state` par défaut), vérifie qu'il a été préparé, puis transmet explicitement cette
même valeur à `deploy.sh` et à Docker Compose. Une valeur personnalisée relative est
résolue depuis la racine du dépôt, jamais depuis le répertoire courant. Compose exige
la variable et n'a aucun fallback relatif. Il monte uniquement ce répertoire sous
`/run/drcloud-deployment:ro`. Il ne monte ni le
checkout `/opt/drcloud-os`, ni `.git`, ni `docker.sock`. Le compte SSH
`drcloud-deploy` est l'unique writer côté hôte. Le répertoire lui appartient avec le
groupe `drcloud-deploy` et le mode `0755`. L'application s'exécute toujours avec
l'utilisateur non privilégié `drcloud` et ne peut que lire le marqueur.

`update.sh` conserve la séquence suivante : build/recréation, health local, health
HTTPS et validation du SHA attendu, puis seulement publication du SHA. La publication
écrit un fichier temporaire de mode `0444` et effectue un renommage atomique. Pendant
les contrôles, le marqueur contient donc encore le dernier succès précédent et
l'Administration peut signaler une divergence. Après succès, les SHA servi et validé
coïncident et la section passe à `ok`.

Le fichier final est volontairement `0444`. Cela ne bloque pas le prochain remplacement :
le renommage atomique dépend des droits d'écriture du propriétaire sur le répertoire
`0755`, et non des droits d'écriture sur l'ancien fichier. Si un VPS existant a un
owner ou un mode différent, exécuter exactement une fois
`sudo /opt/drcloud-os/deploy/ovh/prepare-deployment-state.sh`; la commande est
idempotente et aucune intervention root n'est requise ensuite.

Si la cible échoue, son SHA n'est jamais publié. Le checkout revient au SHA précédent,
le service est reconstruit et les deux contrôles sont rejoués ; ce SHA n'est republié
qu'après réussite du rollback. La base SQLite reste volontairement telle quelle. Si le
rollback échoue, le marqueur n'est pas modifié et aucun faux état OK n'est créé. Sur
une première installation, le répertoire vide est monté : l'API retourne `unknown`
jusqu'au premier déploiement entièrement validé. Un fichier absent, vide ou invalide
reste également `unknown` et n'interrompt pas la page Administration.
