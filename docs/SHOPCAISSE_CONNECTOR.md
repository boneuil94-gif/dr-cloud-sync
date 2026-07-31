# Connecteur ventes ShopCaisse

L'audit du dépôt confirme une API réelle `api.shop-caisse.com/v1` utilisée pour le catalogue, les prix et stocks, mais aucun endpoint de tickets de caisse vérifié. REAL CONNECTORS V1 n'invente donc pas d'API de ventes : il automatise la source réelle disponible, l'export CSV ShopCaisse déposé dans un répertoire protégé.

Configurer `SHOPCAISSE_SALES_INBOX=/data/shopcaisse-inbox`. Les fichiers `.csv` attendent au minimum `sale_id,line_id,sold_at,quantity`; les colonnes facultatives sont `event_kind,item_id,variant_id,ean,reference,unit_price_ttc,unit_price_ht,line_total_ttc,line_total_ht,tax_rate`. `event_kind` accepte notamment `SALE`, `REFUND` et `RETURN`. Une colonne absente reste absente.

Le worker relit les exports toutes les `SHOPCAISSE_SYNC_INTERVAL_SECONDS` (600 secondes par défaut). Les clés ticket + ligne + type d'événement rendent la reprise idempotente. Le mapping existant reste strict : mapping persistant, identifiant article, EAN exact, référence exacte ; aucun fuzzy matching. Les cas `UNMATCHED` et `AMBIGUOUS` restent visibles dans Ventes.

La source est `NOT_CONFIGURED` tant que le répertoire n'existe pas. Son existence signifie que le transport CSV réel est configuré, jamais qu'une API fictive répond.
