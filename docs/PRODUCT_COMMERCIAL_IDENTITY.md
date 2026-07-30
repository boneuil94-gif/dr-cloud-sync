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
