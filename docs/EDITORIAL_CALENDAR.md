# Calendrier éditorial

`/marketing/calendar` propose les contrats mois, semaine et liste; sous 640 px la liste est prioritaire. La recherche serveur est paginée et ses filtres actifs sont supprimables. Contenu, canal, format, campagne/proposition, statut, date, validation et disponibilité provider proviennent des tables existantes.

Les statuts métier restent `DRAFT`, `PROPOSED`, `APPROVED`, `SCHEDULED`, `BLOCKED`, `PUBLISHED`, `MEASURED`, `EXPIRED`, `REJECTED`. En l'absence de provider, toute planification est interne et la publication reste bloquée. Replanification et annulation existantes gardent CSRF, permission, confirmation, idempotence et AuditLog; le drag-and-drop est volontairement différé au profit du flux clavier fiable.
