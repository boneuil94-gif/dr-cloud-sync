# Identité commerciale des produits

## Contrat observé et choix de modèle

Le snapshot PrestaShop reconstruit représente un produit dans `catalogue` et ses unités vendables dans
`declinaisons`. Pour le cas audité, `prestashop:100:710` signifie sans ambiguïté **produit 100,
combinaison 710** : le produit 100 porte le nom parent, tandis que la combinaison 710 porte l'attribut
`AL FAKHER 50K = PEACH ICE`. Les combinaisons 711 à 716 portent respectivement leurs propres valeurs.
Elles n'ont, dans le snapshot, ni référence ni EAN. Ces données ne sont donc pas inventées.

Chaque combinaison étant déjà un `Product` DrCloud autonome et la cible des mouvements, inventaires et
médias, aucun agrégat `ProductVariant` n'est ajouté. Les métadonnées `base_name`, `variant_name` et
`attributes` enrichissent l'unité existante. `drcloud_product_key` n'est jamais recalculée pendant cet
enrichissement.

## Libellé, attributs et provenance

`Product.display_name` est l'unique règle : `base_name — variant_name`, ou `base_name` sans variante.
Les attributs sont un dictionnaire ouvert (groupe vers valeur), et non une notion limitée à la saveur.
Le connecteur PrestaShop obtient les groupes via `product_options`, les valeurs via
`product_option_values`, puis rattache exclusivement les valeurs explicitement listées par la
combinaison.

L'identité est contrôlée par DrCloud. Le nom et la variante observés viennent de PrestaShop lors du
bootstrap déterministe. Une référence de combinaison précède celle du parent. L'EAN de combinaison
précède l'EAN parent dans l'observation externe, mais une valeur canonique DrCloud persistée n'est
jamais écrasée au redémarrage. Les colonnes `*_source` rendent cette politique explicite. ShopCaisse
expose nom, variation, SKU, EAN et parent ; il reste une source de rapprochement explicite, jamais une
raison de remplacer silencieusement la valeur canonique.

## Persistance, conflits et synchronisation

La migration SQLite est additive et idempotente. Elle remplit uniquement les nouvelles métadonnées
vides depuis le snapshot bootstrap ; elle ne recrée aucun produit, ne change aucune clé et ne touche
pas l'historique. Un nouvel appel externe n'est pas lancé au démarrage. Les contraintes existantes
signalent les identités externes dupliquées et les EAN dupliqués restent diagnostiqués, sans fusion.
Une variante sans attribut, référence, EAN ou média demeure valide.

La recherche couvre base, variante, libellé, attributs, référence et EAN. Stock et inventaire résolvent
le même produit stable mais présentent son `display_name`. Les achats continuent de référencer la clé
stable. Une future ligne de vente devra stocker cette clé et pourra conserver un snapshot du libellé
commercial au moment de la vente.

## Médias et usages futurs

`ProductMedia.product_key` rattache déjà chaque image à l'unité vendable. Le média principal et la
galerie restent dédupliqués par empreinte ; l'absence est exposée comme `MISSING`, distincte d'une
image chargée. Les associations d'images de combinaison PrestaShop ne sont pas importées en masse et
ne constituent jamais une preuve d'identité. Cette séparation permet au futur Marketing de joindre
sans ambiguïté produit, variante, média, stock et ventes.

## Audit correctif des sources et hydratation

Le client en lecture seule autorise désormais `products`, `combinations`, `product_options`,
`product_option_values`, `images`, `stock_availables`, `manufacturers` et `suppliers`. Une combinaison
expose son produit parent, son identifiant, `reference`, `ean13`, et, suivant la version PrestaShop,
`upc`, `isbn`, `mpn`, ses associations de valeurs d'options et d'images. La quantité reste fournie par
`stock_availables`; l'activation est celle du parent. Ces données sont observées, sans aucune écriture
PrestaShop. ShopCaisse expose dans son catalogue normalisé le nom, `variation`, `sku`/référence, les
codes-barres et le parent. Il est complémentaire seulement lorsqu'un mapping existant et fiable donne
l'identité DrCloud; aucun rapprochement flou n'est réalisé par l'hydratation.

Le snapshot réel conservé dans ce dépôt confirme que le parent 100 est **AL FAKHER CROWN BAR Hyper
Max Prime 50K**. Les identités stables `prestashop:100:710`, `:711` et `:712` sont les combinaisons
710 **PEACH ICE**, 711 **STRAWBERRY PUNCH** et 712 **LOVE**, sous le groupe ouvert `AL FAKHER 50K`.
Les suivantes observées comprennent 713 **GUM MINT** et 714 **MINT**. Pour 710–714, le snapshot ne
contient ni référence ni EAN; leur stock observé vaut 5. Aucune référence, aucun EAN et aucune image de
variante ne sont donc inventés. L'API live reste l'autorité pour constater une évolution ultérieure.

`ProductHydrationService` persiste d'abord chaque fait dans `product_observations`, puis propose les
champs canoniques. Les valeurs structurées d'options construisent une variante déterministe (`valeur`
ou `valeur · valeur`). Les sources `MANUAL` et `DRCLOUD` protègent une valeur validée; une nouvelle
observation reste consultable et produit un diagnostic au lieu de l'écraser. Un EAN doit être un
GTIN-8/12/13/14 avec checksum valide et unique parmi les produits actifs. Toute divergence entre
sources, surcharge locale divergente ou collision devient `CONFLICT`. Les migrations sont additives,
idempotentes et indexent variante, référence et diagnostic.

Le job durable `CATALOGUE_ENRICHMENT` traite un lot local d'observations avec clé d'idempotence et
publie les compteurs total, enrichis, inchangés, incomplets, conflits et erreurs. Le rendu Catalogue
ne déclenche aucun appel externe. L'import binaire d'une image continue impérativement de passer par
`ProductMediaService`; la présente étape expose la portée parent/combinaison mais ne télécharge pas
automatiquement les médias distants. Une image locale principale reste donc prioritaire.

Le Catalogue affiche les raisons précises, propose les filtres « À compléter », image et variante,
et permet une correction authentifiée, protégée par CSRF et auditée avec
`PRODUCT_COMMERCIAL_DATA_UPDATED`. Le scan EAN demande toujours confirmation et l'upload mobile garde
la validation existante. Les identifiants techniques restent accessibles par l'API de détail mais
sont retirés de la vue métier principale. La clé produit n'est pas éditable.

Les quantités, mouvements, sessions d'inventaire, achats et réceptions ne sont jamais modifiés par ce
service : tous restent attachés à `drcloud_product_key`. Une future ligne Sales devra utiliser cette
clé et pourra figer un libellé historique; Marketing pourra sélectionner l'identité courante enrichie.
Un futur import CSV (`product_key,variant,reference,ean`) devra obligatoirement offrir un aperçu avant
application; il n'est pas inclus dans cette correction.
