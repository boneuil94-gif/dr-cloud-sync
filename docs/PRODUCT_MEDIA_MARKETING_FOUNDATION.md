# ProductMedia et fondation Marketing

## Audit initial — frontières constatées avant la PR K

L'audit du domaine, des repositories, routes, vues, scripts de production et tests établit les frontières suivantes :

- **Catalogue / identité** : `Product` est persistant dans `drcloud_products`; `drcloud_product_key` est l'identité locale durable. Le mapping associe des identités PrestaShop et ShopCaisse stables, jamais le nom. L'archivage est réversible et conserve l'historique.
- **PrestaShop** : le connecteur est GET-only et sait lire produits, combinaisons, options et stocks. Il ne possède aujourd'hui ni ressource `images`, ni contrat authentifié de téléchargement d'image, ni date source fiable. Un import média PrestaShop n'est donc **pas implémenté** : prétendre le rendre reprenable ou juridiquement réutilisable serait faux. Une future commande dédiée utilisera le `product_id`/`combination_id`, jamais le nom, et `JobRun`; aucun téléchargement n'a lieu dans un GET Catalogue.
- **Stock / Inventaire / Achats** : un ledger Stock append-only et une projection locale existent; les réceptions fournisseurs appliquées créent des mouvements traçables. Inventaire conserve l'EAN comme preuve d'identification. Fournisseurs, commandes et réceptions sont persistants.
- **Ventes et marge** : `SALE` est seulement une valeur future de type mouvement. Il n'existe ni Sales ledger, ni lignes de vente, ni série temporelle de ventes fiable, ni marge calculable. Aucun signal ou score Marketing n'est donc produit.
- **Infrastructure** : SQLite, `ActivityLog`, `JobRun`, auth cookie signé, CSRF, shell responsive, scan caméra, PWA network-only pour les API, sauvegarde, restauration documentée et Administration existaient. Le service worker ne précache pas les médias produit.

## IMPLEMENTED NOW

### Domaine et persistance

`ProductMedia` conserve un `media:<UUID>` indépendant du produit, du fichier, d'une URL et de PrestaShop; la relation utilise `product_key`. Il porte type, rôle `PRIMARY|SECONDARY`, source (`PRESTASHOP`, `MANUAL_UPLOAD`, `MOBILE_CAMERA`, `IMPORT`, `AI_GENERATED`), provenance, référence de stockage opaque, MIME, dimensions, taille, SHA-256, dates, état actif, nom nettoyé, type visuel, usages sobres (`catalogue`, `ecommerce`, `marketing`, `social`), droit Marketing (`UNKNOWN|ALLOWED|FORBIDDEN`) et protection de l'original.

Deux tables additives et idempotentes stockent métadonnées et variantes; aucun octet n'est en SQLite. Un index partiel garantit une seule PRIMARY active. Le changement de PRIMARY est une transaction qui rétrograde l'ancienne sans effacer sa provenance. Désactiver est logique; archiver un produit ne touche pas ses médias. Le même SHA-256 sur plusieurs produits reste plusieurs relations et plusieurs identités métier.

### Stockage, validation et diffusion

`LocalMediaStorage` est l'adapter local derrière un contrat à références opaques; il pourra être remplacé par un adapter objet/S3 sans modifier `ProductMedia`. Seul le service compose et résout les références. Chemins absolus, `..`, traversal et identités non UUID sont refusés; l'API ne retourne jamais `storage_reference`.

Valeurs retenues : **10 Mio**, **40 mégapixels**, côté maximal **10 000 px**, côté minimal **16 px**, réserve disque **100 Mio**. Pillow est ajouté car il fournit une validation/décompression éprouvée et la normalisation requise. Seuls JPEG, PNG et WebP détectés par contenu sont acceptés; SVG et MIME/extension trompeurs sont refusés. L'orientation EXIF est appliquée puis l'image est réencodée : EXIF, géolocalisation et métadonnées inutiles ne survivent pas. SHA-256 porte sur les octets normalisés persistés.

`ORIGINAL`, `THUMBNAIL` (boîte 160×160) et `DISPLAY` (boîte 1200×1200) sont réellement générés. Les listes reçoivent seulement une URL versionnée de thumbnail, jamais du base64. La route contrôlée sert MIME et longueur exacts, `nosniff`, et cache immutable un an grâce au checksum dans l'URL. Les pages/API métier restent `no-store`. Le placeholder est HTML/CSS, sans binaire. Les images sont lazy-loaded et le PWA ne les précache pas.

### API, interface et audit

Les contrats authentifiés sont `GET|POST /api/products/<product_key>/media`, `POST .../<media_id>/primary`, `POST .../<media_id>/disable`, plus `GET /media/<media_id>/<variant>`. Toutes les mutations passent le CSRF global, valident produit/source/rôle/contenu et produisent `PRODUCT_MEDIA_ADDED`, `PRODUCT_MEDIA_PRIMARY_CHANGED`, `PRODUCT_MEDIA_DISABLED` (ou `PRODUCT_MEDIA_IMPORTED`). L'octet et le chemin ne sont jamais journalisés.

Catalogue et Inventaire affichent thumbnail/placeholder, identité textuelle complète et aperçu sélectionné. Catalogue accepte un fichier ou la caméra mobile via `capture="environment"`; l'EAN reste l'identification. Stock, lignes de commandes et cartes/réceptions reçoivent des références média compactes dans leurs contrats JSON; leur enrichissement visuel peut progresser sans couplage au filesystem.

### Sauvegarde, restauration et observabilité

La sauvegarde contient désormais le snapshot SQLite **et** l'arbre média avec manifeste taille/SHA-256. Administration ignore pour son statut « OK » les anciens bundles qui ne déclarent pas les médias inclus. La restauration applicative contrôlée valide l'intégralité avant remplacement; la procédure opérateur restaure DB + médias. Les métadonnées sans fichier et médias corrompus remontent en WARNING, sans suppression; les fichiers sans métadonnée restent conservés pour audit. Administration expose actifs, produits avec/sans image, volume, fichiers manquants/corrompus. La capacité disque globale inclut naturellement le répertoire média et l'écriture refuse une réserve insuffisante.

## FUTURE CONTRACT

### De ProductMedia à MarketingAsset

`ProductMedia` est l'original canonique. `MarketingAsset` sera un dérivé de communication et ne remplacera jamais l'original, particulièrement si `protected_original=true`. Workflow cible : `MarketingOpportunity → ProductMedia ALLOWED + BrandKit → CreativeBrief → génération IA → MarketingAsset DRAFT → validation humaine → APPROVED`. BrandKit référencera logo officiel, couleurs, typographies, règles, CTA et presets (Story 1080×1920, portrait 1080×1350, carré 1080×1080, bannière 1120×340, Snapchat, A4, vidéo); cette PR ne modifie aucun logo et n'appelle aucune IA.

### Opportunity Engine explicable

Le moteur attendra des faits réels : ventes/tendance, Stock, réceptions, nouveauté, coûts/marge, dernière publication, campagnes et performances. Il pourra ensuite émettre `TOP_SELLER`, `SALES_ACCELERATION`, `SLOW_MOVING_STOCK`, `BACK_IN_STOCK`, `LARGE_RECEIPT`, `NEW_PRODUCT`, `HIGH_STOCK`, `NOT_RECENTLY_PROMOTED`, `CAMPAIGN_PERFORMER`. Aucun seuil de surstock n'est inventé. Un niveau `HIGH|MEDIUM|LOW` aura toujours des raisons factuelles, jamais un score IA opaque. Stock est un garde-fou : une quasi-rupture ne doit normalement pas être poussée. Saisonnalité, événement ou météo resteront secondaires.

Les Suggestions du jour n'existeront qu'une fois alimentées. L'utilisateur pourra accepter, ignorer, reporter ou modifier. Une corrélation publication/ventes sera formulée comme corrélation, jamais causalité.

### Social, validation et performance

`Campaign`, `SocialPost` et `SocialConnector` ne sont pas persistés avant usage réel. Le connecteur de chaque plateforme annoncera ses capacités effectives; aucune hypothèse Story/Reel n'est faite. États pressentis : `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `SCHEDULED`, `PUBLISHING`, `PUBLISHED`, `FAILED`, `CANCELLED`. Règle : **IA propose, humain valide, DrCloud OS programme/publie**; aucune autopublication initiale.

Les tokens restent serveur, hors frontend/ActivityLog/média, à permissions minimales, avec expiration, rotation et révocation. Le futur calendrier montrera brouillons, validations, planifications, publications et échecs. Selon les API, `PerformanceMetric` conservera plateforme, identifiant distant, collecte/période et impressions, reach, likes, commentaires, partages, clics et vues. Boucle cible : Opportunity → Creative → Publication → Performance → Ventes → recommandations explicables.

### Étapes techniques recommandées

1. Ajouter au connecteur PrestaShop un contrat images GET-only documenté et testé, puis un job d'import local idempotent/reprenable basé sur identité source + checksum, avec erreurs réseau et fraîcheur.
2. Construire un Sales ledger local idempotent avant tout Opportunity Engine.
3. Enrichir ensuite les vues compactes Stock/Achats/Réceptions et la recherche globale; OCR/vision ne devra fournir qu'une proposition, jamais créer/réceptionner seul.
4. Seulement après les faits Ventes, implémenter MarketingOpportunity et feedback humain; puis MarketingAsset/BrandKit/approval avant les connecteurs sociaux.
