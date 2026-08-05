# Connecteur SumUp : contrat, couverture et limites

Audit revérifié le **3 août 2026** à partir du contrat public SumUp et du code
déployable. Aucun secret Dr Cloud n'est présent dans le dépôt ou l'environnement
d'audit : l'accessibilité effective du compte ne peut donc pas être affirmée.
Une réponse `403` doit être classée `SCOPE_MISSING`, jamais contournée.

Sources officielles : [API SumUp](https://developer.sumup.com/api),
[authentification/scopes](https://developer.sumup.com/api/authentication),
[Readers API](https://developer.sumup.com/terminal-payments/introduction/).

## Endpoints GET implémentés

| Ressource | Endpoint | Scope/permission attendu | État |
|---|---|---|---|
| Merchant | `GET /v0.1/me` | profil en lecture (`user.app-settings`) | implémenté, à homologuer compte |
| Historique | `GET /v2.1/merchants/{merchant_code}/transactions/history` | `transactions.history` | implémenté |
| Détail | `GET /v2.1/merchants/{merchant_code}/transactions?id={id}` | `transactions.history` | implémenté |
| Payout events | `GET /v1.0/merchants/{merchant_code}/payouts?start_date&end_date&format=json` | `payouts` | implémenté |
| Readers | `GET /v0.1/merchants/{merchant_code}/readers` | `payment_instruments` | implémenté, conditionnel |

Le connecteur n'appelle aucun endpoint d'écriture. Les refunds, chargebacks,
reversals, tips, taxes, frais et rattachements payout ne sont pas supposés avoir
des endpoints de liste indépendants : ils sont extraits du détail transaction ou
des événements payout quand ils y figurent.

## Matrice du contrat réel

`R` = récupéré, `N` = normalisé, `P` = persisté. Le payload brut filtré est
toujours persisté, ce qui évite qu'un champ métier additionnel soit perdu.

| Domaine | Endpoint | Champ | Disponible API | R | N | P | Utilisé | Manquant / raison |
|---|---|---|---:|---:|---:|---:|---|---|
| Merchant | `/v0.1/me` | merchant code, legal/trading name, country, currency, timezone, status | conditionnel | oui | oui | oui | identité | `SCOPE_MISSING` si 403 |
| Merchant | `/v0.1/me` | payout settings | si présent | oui | oui | oui | diagnostic | `API_NOT_EXPOSED` si absent |
| Transaction | history/detail | id/code, amount/currency, timestamp, status/simple status | oui | oui | oui | oui | Finance/rapprochement | — |
| Transaction | detail | payment/entry/card type, terminal, client/foreign ids | si présent | oui | oui | oui | rapprochement | `API_NOT_EXPOSED` si absent |
| Transaction | detail | product summary, VAT, tip, reference/description, receipt URL | si présent | oui | oui | oui | diagnostic/Finance | `API_NOT_EXPOSED` si absent |
| Event | detail | historique, refunds, reversals, chargebacks, payout events | si présent | oui | oui | oui | Finance/settlement | champ absent : `API_NOT_EXPOSED` |
| Reversal | detail events | id, transaction, montant/devise, date/statut, raison, impact payout | si présent | oui | oui | oui | sous-ledger distinct | champ absent : `API_NOT_EXPOSED` |
| Fee | detail | type, amount, currency, adjustments | si présent | oui | oui | oui | Finance | ajustement seulement si exposé |
| Refund | detail events | id, original tx, amount/date/status, partial/full, reason | si présent | oui | oui | oui | Finance | reason `API_NOT_EXPOSED` si absent |
| Chargeback | detail events | id, original tx, amount/date/status/reason | si présent | oui | oui | oui | Finance | dispute autonome `API_NOT_EXPOSED` |
| Payout | payouts | id/date/status/amount/currency/fee/reference | oui selon scope | oui | oui | oui | settlement | — |
| Payout | payouts | period, paid date, included items/deductions/adjustments | si présent | oui | oui | oui | settlement | réserve/balance `API_NOT_EXPOSED` si absent |
| Reader | readers | id/name/model/status/store/last seen/software | selon produit/scope | oui | oui | oui | Data Hub | `UNSUPPORTED` si compte sans Readers API |
| Webhook | — | événement/signature/retry | non retenu | non | non | non | aucun | `API_NOT_EXPOSED` pour ces lectures; polling déterministe |

Les données carte sensibles (PAN complet, CVV, token d'autorisation) ne sont ni
nécessaires ni conservées. Le filtre est récursif pour les secrets ; seules les
métadonnées carte non sensibles éventuellement renvoyées sont admises.

## Persistence et idempotence

Les tables sont volontairement séparées : `sumup_merchants`,
`sumup_transactions`, `sumup_transaction_events`, `sumup_fees`,
`sumup_refunds`, `sumup_chargebacks`, `sumup_reversals`, `sumup_payouts`, `sumup_payout_items`,
`sumup_readers` et `payment_settlements`. Elles ne sont ni le Sales Ledger, ni
le Bank Ledger/Qonto. Les identifiants fournisseur sont les clés primaires ; à
défaut, une empreinte déterministe est employée. Les upserts rendent le replay
idempotent.

Un lien transaction → payout n'est `MATCHED` que sur transaction id/code exact.
Une heuristique faible ne produit jamais de match automatique. Payout → crédit
Qonto reste à homologuer. Le CA reste exclusivement issu du Sales Ledger.

## Backfill contrôlé

* transactions : `oldest_time`/`newest_time`, pages de 100 maximum, détail pour
  chaque ligne, watermark persistant et chevauchement configurable ;
* payouts : `start_date` et `end_date` obligatoires, fenêtres de 31 jours par
  défaut, offset persistant, chevauchement à la frontière et reprise ;
* aucune limite globale à 100 ; le curseur est sauvegardé par le Data Hub ;
* avant un très gros backfill, exécuter des fenêtres courtes pour estimer le
  volume et la durée (un appel détail est fait par transaction).

SumUp ne publie pas de quota universel garanti ni de fenêtre de rétention unique
dans le contrat consommé : `429` respecte `Retry-After`, les `5xx` et timeouts
sont retentés avec backoff. La profondeur réellement retournée doit être mesurée
sur le compte et consignée comme couverture historique ; elle n'est pas inventée.

## Audit de couverture final

| Domaine | Champs audités | Complets | Partiels | Non exposés/conditionnels | Couverture code |
|---|---:|---:|---:|---:|---:|
| Merchant | 7 | 7 | 0 | 0 | 100 % |
| Transactions/détail | 27 | 27 | 0 | 0 | 100 % |
| Refund/chargeback/reversal/event | 27 | 27 | 0 | 0 | 100 % |
| Fees | 4 | 4 | 0 | 0 | 100 % |
| Payout/items | 18 | 18 | 0 | 0 | 100 % |
| Readers | 8 | 8 | 0 | 0 | 100 % |
| Webhooks/réserves/disputes autonomes | 3 | 0 | 0 | 3 | 0 % (`API_NOT_EXPOSED`) |

Cette couverture mesure le **contrat géré par le code**, pas les données du
compte. Après homologation, chaque champ absent doit être classé parmi
`API_NOT_EXPOSED`, `SCOPE_MISSING`, `ENDPOINT_NOT_IMPLEMENTED`,
`CLIENT_IGNORES_FIELD`, `PROVIDER_DROPS_FIELD`, `MODEL_MISSING`,
`LEDGER_MISSING`, `UNSUPPORTED` ou `UNKNOWN` dans le diagnostic d'exploitation.

### Couverture avant/après

Avant : transactions aplaties et payouts, sans sous-ledgers ; dates payout non
bornées. Après : 5 endpoints GET, 11 tables indépendantes, événements/frais/
refunds/chargebacks/items typés, payload complet filtré, fenêtres et reprise.
L'historique importé pendant cet audit est **0** (aucun credential de production,
donc aucun appel au compte), et cette limite est explicitement observable.

## Politique de migrations SQLite additives

Les migrations du connecteur SumUp sont strictement additives et idempotentes : au démarrage, les tables déclarées sont créées si elles n’existent pas, puis chaque colonne attendue est ajoutée uniquement lorsqu’elle manque. Les lignes existantes sont conservées. Les colonnes historiques dont la valeur métier est inconnue, comme `sumup_transactions.simple_status`, restent `NULL` pour éviter un faux statut.

Aucune migration de production ne doit utiliser `DROP TABLE`, recréer une table contenant des données, ou renommer destructivement une colonne. Les colonnes `NOT NULL` ajoutées à un historique doivent avoir un défaut neutre technique seulement lorsque SQLite l’exige et que les écritures applicatives fournissent ensuite explicitement la valeur métier.

## Diagnostic central de schéma

L’administration expose `/api/admin/sumup-schema`. Ce contrôle est en lecture seule et compare les schémas attendus aux tables de production sensibles : `sumup_transactions`, `sumup_payouts`, tables bancaires, sales ledger, settlements et `data_sources`. Le résultat inclut le statut global (`OK`, `DRIFT`, `UNAVAILABLE`, `ERROR`), les tables en dérive, les colonnes manquantes, les colonnes supplémentaires informatives, la date du contrôle et les empreintes attendue/observée.

## Récupération en cas de `DRIFT`

1. Ne supprimez et ne réinitialisez aucune donnée.
2. Consultez Administration → Intégrité schéma SQLite pour identifier la table et les colonnes manquantes.
3. Relancez l’application ou la commande de migration SumUp afin d’appliquer uniquement les migrations additives sûres.
4. Relancez le diagnostic et vérifiez que le statut redevient `OK`.
5. Si une dérive persiste sur un module secondaire, seul le job concerné doit rester bloqué avec `SCHEMA_DRIFT`; le reste de l’application reste disponible.
