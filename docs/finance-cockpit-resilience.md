# Finance Cockpit — diagnostic et mode dégradé

## Cause racine

La route `GET /api/finance/cockpit` appelait un unique calcul monolithique. Ce
calcul interrogeait successivement `sale_events`, les tables SumUp, le read model
des settlements et, lorsqu'un ancien compte Qonto subsistait en base,
`summary()`. Aucune de ces lectures n'avait de frontière d'erreur. Une seule
`sqlite3.OperationalError` (notamment un schéma SumUp/settlement ancien auquel il
manque une colonne attendue), ou une erreur du ledger optionnel, remontait donc
jusqu'au routeur. Le routeur transformait cette exception en HTTP 500 et le
frontend remplaçait alors **toutes** les données par « Finance indisponible ».

Qonto était en outre déduit de la présence historique d'une ligne
`bank_accounts`. Cette présence ne prouve pas que le credential courant est
valide : elle pouvait rendre la banque « configurée » alors que le health check
avait échoué.

Ce diagnostic est celui du chemin de code déployable. Les logs et la réponse de
l'instance de production ne sont pas accessibles depuis ce dépôt ; aucune
valeur de credential ou donnée métier de production n'a donc été consultée.

## Correction

Les sept blocs (`revenue`, `channels`, `payments`, `margins`, `settlements`,
`products`, `bank`) possèdent maintenant chacun leur frontière d'erreur et leur
contrat d'état. Une erreur est journalisée avec le nom de section et la classe
d'exception seulement ; ni SQL, ni contenu de ligne, ni secret n'est inclus.

Le statut global est :

* `FRESH` seulement si tous les blocs sont frais ;
* `PARTIAL` dès qu'au moins un bloc exploitable cohabite avec une source absente,
  ancienne ou en erreur ;
* `UNAVAILABLE` si aucun bloc métier n'est exploitable.

La banque s'appuie sur le résultat du health check Qonto du runtime. Un compte
historique ne contourne plus ce contrôle. Sans authentification valide, elle
retourne `NOT_CONFIGURED` et aucune estimation de solde.

Une table vide n'est plus assimilée à un zéro réel. Le zéro n'est rendu que si
le ledger contient une preuve de source et qu'aucun événement ne tombe dans la
période (ou si les événements de la période totalisent réellement zéro).

## Validation après déploiement

1. Appeler `GET /api/finance/cockpit` avec un compte ayant `finance.read` et
   vérifier HTTP 200 et `status: PARTIAL`.
2. Vérifier `revenue.status`, `payments.status` et leurs valeurs non nulles pour
   les ledgers effectivement alimentés.
3. Vérifier `bank.status: NOT_CONFIGURED`, `bank.balance: null`.
4. Ouvrir `/finance` et vérifier que les cartes connues restent visibles, que
   chaque carte a son badge et que « Top produits » est masqué sans données.
5. Contrôler les logs : une panne locale éventuelle doit produire
   `finance cockpit section failed section=<section> error_code=<code>` sans
   payload métier ni credential.
