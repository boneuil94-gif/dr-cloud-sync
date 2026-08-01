# Connecteurs fournisseurs

L'audit n'a trouvé aucun accès fournisseur API, IMAP, SFTP ou facture électronique homologué. La source Data Hub `supplier_documents` est donc **NOT_CONFIGURED**. Aucun connecteur générique ni OCR prétendument fiable n'est créé.

Le contrat d'activation futur est read-only, par `secret_ref` résolu serveur : une source structurée validée peut créer `SupplierInvoice` et ses lignes, puis validation, Purchase Cost Ledger, TVA et Finance. Email autorisera seulement la lecture des pièces jointes (facture, confirmation, bon de livraison), jamais l'envoi ni la suppression. Un PDF/image non structuré reste `PREVIEW` jusqu'à validation humaine. Cadence centrale proposée : `SUPPLIER_SYNC_INTERVAL_SECONDS=3600`.
