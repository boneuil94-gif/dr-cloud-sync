# Audit des données CRM disponibles — 10 août 2026

Cet audit décrit **le contrat présent dans ce dépôt**, et non les champs que les
éditeurs pourraient éventuellement fournir. Une valeur absente reste `NULL` /
`UNKNOWN`.

| Source réellement intégrée | Identifiant client provider | identité / contact | historique d'achat | canal | consentement | conclusion |
|---|---|---|---|---|---|---|
| ShopCaisse `/stores/{id}/sales` | non conservé dans `SaleEvent` | aucun champ contractuel client audité | date, commande, ligne, produit, quantité et montants selon réponse | `SHOPCAISSE` / magasin | aucun | ventes exploitables, clients non importables avec le contrat actuel |
| PrestaShop `orders`, `order_details` | `id_customer` peut exister dans la réponse distante mais n'est pas conservé par le normaliseur actuel | la ressource `customers` n'est pas autorisée par `PrestaShopClient.RESOURCES` | date, commande, ligne, produit, quantité, montants | `PRESTASHOP` / web | aucun dans le ledger | ventes exploitables; import clients à implémenter après validation du contrat production |
| Sales Ledger `sale_events` | aucun | aucun | source, id commande/ligne, date, produit, quantité, TTC/HT, coût connu, devise, canal, magasin | oui, nullable | aucun | autorité unique du CA CRM; une vente anonyme ne crée jamais un client |
| Paiements SumUp / Qonto / rapprochements | aucun client CRM fiable | aucun | transaction/payout/rapprochement, pas un achat client | provider de paiement | aucun | ne doit pas créer ni enrichir une identité client |
| `crm_customers` / références externes | `provider + external_id`, uniquement après ingestion explicite | nom, prénom, email/téléphone normalisés, naissance/langue/pays/ville/CP si fournis | via lien déterministe `crm_sale_links` | via ventes liées | événements de consentement sourcés | stockage prêt, mais aucune donnée client production n'est livrée dans le dépôt |

## Règles de qualité et rapprochement

* `provider + external_id` rejoué produit la même identité interne.
* email **et** téléphone exacts et normalisés peuvent produire `MATCHED`.
* un seul contact exact produit `PROBABLE` et exige une validation humaine.
* des preuves contradictoires doivent être classées `AMBIGUOUS`; les profils sans
  preuve commune sont `DISTINCT`. Le nom seul n'est jamais une clé de fusion.
* le consentement marketing expose `marketing_consent`, `consent_source`,
  `consent_at` et `revoked_at`. Sans événement explicite, il vaut `UNKNOWN`, ce
  qui bloque toute sollicitation.

## Seuils RFM configurables (valeurs initiales v1)

Les valeurs initiales sont recency `30 / 60 / 90 / 180` jours, frequency
`1 / 2 / 3 / 6 / 10` commandes et monetary `0 / 50 / 200 / 500 / 1000` euros
TTC observés. VIP requiert au moins 6 commandes et 500 €; réactivation après 90
jours; perdu après 180 jours. Elles sont stockées dans `crm_rfm_settings`,
versionnées et modifiables via `configure_rfm`. Un client sans vente liée ne
reçoit aucun segment.

## Limites de production, à ne pas masquer

Aucune base de production n'est incluse dans le dépôt. Le nombre réel de clients,
la couverture des ventes et du CA, les consentements et la répartition RFM ne
peuvent donc pas être annoncés ici. Les catégories favorites restent `NULL`, car
`sale_events` ne contient pas de catégorie. Aucun provider d'envoi marketing,
aucun export client, aucune activation de points, aucune prédiction de LTV et
aucune attribution causale de campagne ne sont livrés. Le prochain réachat n'est
calculé qu'avec au moins deux dates distinctes et porte explicitement le statut
`PREDICTED`; sinon il vaut `UNKNOWN`.
