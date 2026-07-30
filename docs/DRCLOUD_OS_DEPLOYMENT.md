# Déploiement de DrCloud OS

## Audit de l'existant et choix

DrCloud OS est une application **WSGI Python sans framework**, exposée par `InventoryApp`; la commande historique `inventory-serve` utilisait `wsgiref` sur `127.0.0.1:8080`. Les fichiers HTML/CSS/JS sont dans `src/dr_cloud_sync/static`. `InventoryService` charge le mapping final et son rapport, tandis que l'inventaire, le catalogue central et l'audit utilisent SQLite. Les anciens chemins sont réglés par `INVENTORY_CATALOGUE`, `INVENTORY_MAPPING_REPORT`, `INVENTORY_DATABASE`, `INVENTORY_HOST` et `INVENTORY_PORT`.

La production conserve l'application et SQLite, mais utilise **Waitress**, `HOST=0.0.0.0`, `PORT=8080` et `/data/drcloud.db`. `/data` doit impérativement être un volume persistant. `os-serve` exige les trois variables d'authentification. Les logs techniques datés vont vers stdout/stderr; ne jamais transmettre mots de passe, cookies, `Authorization` ou clés API aux logs.

## Configuration

| Variable | Rôle / valeur initiale sûre |
|---|---|
| `DRCLOUD_ENV` | `production` en production |
| `DRCLOUD_SECRET_KEY` | secret aléatoire d'au moins 32 caractères |
| `DRCLOUD_ADMIN_USERNAME` | compte administrateur initial |
| `DRCLOUD_ADMIN_PASSWORD` | mot de passe long, fourni uniquement par secret manager |
| `DRCLOUD_DATA_DIR` | `/data` |
| `HOST`, `PORT` | `0.0.0.0`, `8080` |
| `DRCLOUD_SAFE_MODE` | **`true`**; bloque toute écriture externe |
| `BARCODE_SYNC_MODE` | **`dry-run`** |
| `DRCLOUD_TRUST_PROXY` | `true` uniquement si l'application n'est joignable que via le proxy contrôlé |
| `PRESTASHOP_API_URL`, `PRESTASHOP_API_KEY` | connecteur serveur; clé GET seulement au premier lancement |
| `SHOPCAISSE_API_KEY`, `SHOPCAISSE_COMPANY_ID` | connecteur serveur, facultatif; jamais côté navigateur |

Ne pas inclure de `.env` dans l'image. En production, le cookie de session est `HttpOnly`, `Secure`, `SameSite=Lax`; HTTPS est donc obligatoire. La protection CSRF couvre les mutations. Le login est limité à cinq échecs par adresse sur cinq minutes. Les en-têtes CSP, anti-sniffing, anti-frame et Referrer-Policy sont activés. Activer HSTS **sur le reverse proxy seulement après validation HTTPS**, idéalement avec `max-age=31536000` sans `includeSubDomains` avant audit de tous les sous-domaines.

## Préparation des données (explicite et idempotente)

Le mapping validé n'est jamais téléchargé depuis un artifact temporaire au démarrage. Avant le service, déposer sur le serveur les fichiers validés puis exécuter :

```bash
INVENTORY_CATALOGUE=/secure-import/mapping-prestashop-shopcaisse-final.json \
INVENTORY_MAPPING_REPORT=/secure-import/rapport-mapping-final.json \
DRCLOUD_DATA_DIR=/data dr-cloud-sync os-init-catalog
```

La commande exige un mapping validé et non vide, initialise `schema_version`, puis insère uniquement les identités absentes par `drcloud_product_key`. Elle ne contacte aucun connecteur, ne remplace pas les produits existants et est rejouable sans doublon.

## Option A — VPS, Docker et proxy HTTPS (recommandée aujourd'hui)

Cette option est prévisible et économique, donne le contrôle du volume et des sauvegardes, mais demande les mises à jour système et la supervision. Installer Docker sur un VPS, attacher un disque sauvegardé, puis :

```bash
docker build -t drcloud-os:1.0.0 .
docker volume create drcloud-data
# importer d'abord les fichiers validés dans un répertoire éphémère monté en lecture seule
docker run --rm -v drcloud-data:/data -v "$PWD/validated:/import:ro" \
  -e DRCLOUD_DATA_DIR=/data \
  -e INVENTORY_CATALOGUE=/import/mapping-prestashop-shopcaisse-final.json \
  -e INVENTORY_MAPPING_REPORT=/import/rapport-mapping-final.json \
  drcloud-os:1.0.0 dr-cloud-sync os-init-catalog

docker run -d --name drcloud-os --restart unless-stopped -p 127.0.0.1:8080:8080 \
  -v drcloud-data:/data --env-file /etc/drcloud/os.env drcloud-os:1.0.0
curl --fail http://127.0.0.1:8080/health
```

Configurer Caddy, nginx ou Traefik pour terminer TLS et transmettre `Host`, `X-Forwarded-Proto` et `X-Forwarded-For`. Le port Waitress reste lié à localhost/réseau Docker privé. Mettre `DRCLOUD_TRUST_PROXY=true` uniquement dans cette topologie et filtrer tout accès direct. Le proxy redirige HTTP vers HTTPS, limite aussi la taille à 1 Mio, puis ajoute HSTS après validation. Aucun certificat n'est géré dans le code.

## Option B — plateforme de conteneurs managée

Déployer la même image sur une plateforme proposant **un volume persistant réellement montable sur `/data`**, des secrets, HTTPS et sauvegardes. C'est plus simple pour TLS, mises à jour d'hôte et disponibilité, mais souvent plus coûteux; les volumes peuvent imposer une région, une seule réplique ou une procédure de snapshot propre au fournisseur. Ne pas sélectionner une offre à filesystem éphémère et ne pas multiplier les répliques partageant SQLite. Vérifier restauration et export du volume avant le go-live.

**Recommandation : option A**, un petit VPS avec Docker, proxy HTTPS et volume/snapshots, adaptée à cette V1 SQLite mono-instance. Aucun compte ou fournisseur existant n'est supposé.

## Domaine et HTTPS futurs

Aucune action DNS n'est réalisée ici. Après validation du serveur : créer l'enregistrement de `os.drcloud.fr` vers l'hébergeur, configurer le vhost du reverse proxy, obtenir/renouveler le certificat, tester la chaîne TLS, puis seulement publier l'application. Flux : **DNS → hébergeur → reverse proxy → HTTPS → DrCloud OS**.

## Premier déploiement sûr

1. Préparer et durcir le serveur, sans exposition publique de `8080`.
2. Configurer les secrets hors image; conserver `DRCLOUD_SAFE_MODE=true` et `BARCODE_SYNC_MODE=dry-run`.
3. Créer/mounter le volume persistant `/data`.
4. Importer le mapping validé, démarrer l'application derrière le proxy.
5. Vérifier `GET /health` (`status`, version et DB; aucun appel externe).
6. Vérifier login, cookie sécurisé, échec de login et logout.
7. Vérifier que l'import annonce exactement 478 produits et qu'une seconde exécution reste à 478.
8. Vérifier Roadmap authentifiée.
9. Vérifier Catalogue authentifié.
10. Vérifier Inventaire authentifié et persistance après redémarrage.
11. Tester téléphone et tablette via HTTPS. La caméra (`getUserMedia`/`BarcodeDetector`) exige un contexte HTTPS et dépend du navigateur; saisie EAN et douchette Bluetooth/USB restent les fallbacks.
12. Exécuter et restaurer une sauvegarde de test.
13. Seulement après revue séparée envisager les connecteurs live. **Ne pas désactiver SAFE_MODE, activer les EAN live ou écrire les stocks durant ce premier déploiement.**

La PWA fournit manifeste, icône et installation à l'écran d'accueil, sans service worker ni cache offline : aucune saisie n'est présentée comme synchronisée si le serveur ne l'a pas reçue.

## Sauvegarde et restauration

```bash
DRCLOUD_DATA_DIR=/data DRCLOUD_BACKUP_DIR=/backups dr-cloud-sync os-backup
```

La commande utilise l'API de sauvegarde SQLite et crée `drcloud.db` plus `metadata.json` (version et configuration non secrète seulement). La présence de la commande ne signifie pas qu'une sauvegarde a été exécutée : automatiser, chiffrer hors hôte et tester la restauration.

Restauration : arrêter le conteneur, conserver une copie du volume actuel, vérifier `metadata.json`, copier le `drcloud.db` sauvegardé vers `/data/drcloud.db` avec l'identité non-root, redémarrer, vérifier `/health`, login, compteur catalogue et inventaire. Ne jamais restaurer par-dessus une application en écriture.

## Exploitation

`/health` est volontairement public et local, sans secrets ni appels externes. Les erreurs 401/403/404/500 n'affichent aucune trace. Utiliser l'identifiant `X-Request-ID` pour corréler les logs techniques; `ActivityLog` reste l'audit métier. Planifier mises à jour d'image, surveillance disque, rotation/rétention des logs, sauvegardes quotidiennes et exercices de restauration.
