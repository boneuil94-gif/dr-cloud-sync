# API ShopCaisse — contrat réellement attesté

Le client unique est `ShopCaisseClient` dans `shopcaisse.py`, base `https://api.shop-caisse.com/v1`. Il réutilise la clé runtime historique et les routes catalogue déjà éprouvées (`/companies`, `/companies/{id}/items`, détails/prix/stock selon le client). Les retries HTTP, timeout, pagination et erreurs assainies restent centralisés dans ce client. Aucun second credential n'est créé.

Aucun endpoint réseau de tickets, paiements ou remboursements n'est vérifié dans le code, les fixtures ou la configuration de ce dépôt. V1 refuse donc d'en inventer un. La lecture automatique des ventes repose temporairement sur `ShopCaisseSalesProvider`, qui surveille l'inbox protégée `SHOPCAISSE_SALES_INBOX`; le CSV est une source de secours réelle mais rend l'état **PARTIAL**, jamais un faux CONNECTED API.

Les événements `SALE`, `REFUND`, `RETURN`, `CANCELLATION`, `ADJUSTMENT` sont append-only et dédupliqués par source, ticket, ligne et type. Colonnes absentes, paiements compris, restent absentes. Cadence `SHOPCAISSE_SYNC_INTERVAL_SECONDS` (600 s). Une activation API ventes exige d'abord documentation fournisseur et fixture capturée sans secret, puis extension du client existant.

## Conclusion de l’audit d’activation
Les workflows historiques prouvent le client `ShopCaisseClient`, l’authentification Bearer, `/v1/authentication`, `/v1/companies` et les ressources catalogue. Ils ne prouvent aucun endpoint tickets/ventes/paiements/remboursements. En conséquence, aucun second client et aucune route supposée ne sont ajoutés : l’inbox CSV demeure un fallback automatique, et une carte API ventes ne doit pas être déclarée CONNECTED avant homologation d’un contrat réel.
