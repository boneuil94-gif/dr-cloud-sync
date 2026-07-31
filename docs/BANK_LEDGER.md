# Bank Ledger V1

`BankProviderPort` définit exclusivement `health`, comptes, soldes et transactions paginées. Le ledger conserve les transactions séparément des soldes d'autorité. Les montants sont des décimaux sérialisés, la direction est dérivée du signe et les métadonnées dont le nom évoque token, secret, mot de passe ou autorisation sont exclues.

L'idempotence préfère `(provider, external_transaction_id)`. Sans identifiant externe, un SHA-256 déterministe combine provider, compte, date comptable, montant, devise, référence et libellé. Une resynchronisation fait donc `INSERT OR IGNORE`. Les soldes provider (`current`, `available`, devise, timestamp) ne sont jamais reconstruits depuis les mouvements.

## Qonto

`DisabledQontoProvider` est volontairement `NOT_CONFIGURED`. Pour une activation réelle il manque : un contrat/API Qonto officiel validé pour la version retenue, les identifiants read-only injectés par l'infrastructure de secrets, l'identifiant d'organisation requis par ce contrat, et des tests de contrat comptes/soldes/transactions/pagination/rate-limit. Aucun endpoint n'a été inventé. L'adapter futur ne doit exposer ni virement, ni paiement, ni bénéficiaire, ni mutation bancaire. Revolut Business reste une extension future du même port.

Les erreurs réseau, timeouts, 5xx et rate limits pourront porter `retryable=True` et respecter `Retry-After`; auth/config/validation restent terminales. Aucun credential ne doit rejoindre frontend, logs, audits ou tables métier.
