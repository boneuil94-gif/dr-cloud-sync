# Import des images produit PrestaShop

## Contrat et frontière externe

La ressource de métadonnées `products` expose les images parentes dans
`associations.images`; `combinations` expose de la même manière les associations
explicites de chaque combinaison. L'identité locale est résolue exclusivement par
le couple `product_id` / `combination_id`. Aucun nom, ordre ou URL approchante
n'intervient.

Le binaire ne vient pas de `images?display=full`. Le Webservice PrestaShop utilise
un endpoint d'image distinct : `GET /api/images/products/{product_id}/{image_id}`.
Un type d'image déclaré par la boutique peut être demandé avec le segment final
`/{image_type}`. Ce contrat retourne un flux binaire et son `Content-Type`, pas une
ressource JSON. Le client DrCloud n'implémente que `GET`, authentifié côté backend;
la clé et l'URL distante ne sont jamais exposées au navigateur.

## Décision et provenance

L'ordre est : PRIMARY local existant, association unique de combinaison, image
parente (`id_default_image`, ou association parente unique), puis placeholder. Une
association multiple sans cover déterministe est `AMBIGUOUS` et n'est pas importée
comme PRIMARY. La provenance sérialisée conserve la ressource, l'image, le produit,
la combinaison et le rôle `COMBINATION_IMAGE`, `PARENT_IMAGE` ou
`PARENT_FALLBACK`.

Les imports ont `source=PRESTASHOP`, `marketing_usage=UNKNOWN` et
`protected_original=true`. Ils ne sont donc jamais implicitement approuvés pour
Creative AI. Une future intégration Marketing devra exiger une approbation humaine
explicite avant génération, tout en respectant l'original protégé.

## Pipeline local

`ProductMediaService` valide JPEG/PNG/WebP par décodage réel, limite octets,
dimensions et pixels, applique l'orientation, supprime les métadonnées lors de la
normalisation, calcule SHA-256 et produit `ORIGINAL`, `THUMBNAIL` (160 px) et
`DISPLAY` (1200 px). Les URLs internes sont versionnées par checksum et servies
avec un cache immutable; aucune image distante n'est une dépendance d'affichage et
le service worker ne précache pas la bibliothèque.

Les fichiers résident sous `$DRCLOUD_DATA_DIR/media/products`, donc hors release
éphémère. Les bundles de sauvegarde incluent `$DRCLOUD_DATA_DIR/media` avec taille
et SHA-256, et la restauration vérifie le manifeste avant publication. Le stockage
refuse les traversées de chemin et chaque écriture vérifie une réserve disque. Les
produits archivés et leur historique média ne sont pas supprimés.

## Exploitation après merge

1. Vérifier le volume persistant de `DRCLOUD_DATA_DIR`, l'espace libre, la santé du
   stockage de sauvegarde et les secrets `PRESTASHOP_API_URL` / `PRESTASHOP_API_KEY`.
2. Créer et vérifier une sauvegarde avant la première opération.
3. Dans Administration > Média Catalogue, lancer **Analyser les images**. PREVIEW
   est sans mutation et affiche les candidats, fallbacks, PRIMARY existants,
   absences, ambiguïtés et téléchargements requis.
4. Examiner les ambiguïtés, puis déclencher explicitement **Importer les images
   sûres**. Chaque job `PRODUCT_MEDIA_IMPORT` traite au plus 25 candidats; relancer
   PREVIEW/APPLY pour reprendre les lots suivants. Les erreurs unitaires n'arrêtent
   pas le lot.
5. Créer et vérifier un nouveau bundle après import, puis effectuer un restore de
   recette et contrôler DB, original et thumbnail avant toute exploitation.

PrestaShop ne donnant pas ici de checksum/version source, aucun refresh automatique
n'est effectué. Une référence déjà importée est ignorée; tout remplacement futur
doit passer par un nouveau PREVIEW manuel et ne doit jamais écraser un PRIMARY
manuel. Cette PR ne lance ni import de production, ni déploiement, ni IA Marketing.
