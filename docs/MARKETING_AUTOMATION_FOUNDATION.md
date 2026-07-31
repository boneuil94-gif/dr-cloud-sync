# Marketing Automation Foundation

## Architecture livrée

`MarketingAutopilot` orchestre une chaîne déterministe **signaux → opportunités
explicables → propositions DRAFT → creative briefs → validation → calendrier**.
Le PREVIEW exécute la même détection sans mutation. L'autopilot est désactivé par
défaut, limité à trois propositions par jour et toute proposition requiert une
approbation humaine. Aucun adaptateur de publication externe n'est implémenté.

La première règle seed exploite exclusivement deux faits canoniques : produit
`ACTIVE` et média `PRIMARY`. Elle produit au plus une opportunité
`PRODUCT_SPOTLIGHT` par identité média/produit, avec un cooldown prévu de sept
jours. Les ventes, stocks marketing, météo, calendrier et événements restent
explicitement indisponibles : leur absence ne vaut ni zéro, ni signal.

## Contrats et sécurité

- `CreativeGeneratorPort` et `CopyGeneratorPort` isolent les futurs fournisseurs IA.
- `SocialPublisherPort` expose les capacités variables de chaque canal ; aucun
  connecteur Instagram, Facebook, Snapchat ou TikTok ne réalise d'appel.
- `SecretProvider` impose une référence opaque aux credentials. La table
  `social_connections` ne possède aucune colonne token.
- `CompliancePolicy` est un passage obligatoire futur. Les règles vape/chicha
  doivent être validées juridiquement avant toute autopublication ; cette PR
  n'invente aucune règle de droit.
- `PRESERVE_ORIGINAL` protège le packaging : placement, détourage et mise à
  l'échelle seront possibles, mais pas la modification du logo, des couleurs,
  inscriptions ou saveurs.
- chaque création, score, transition, activation et programmation est auditée ;
  les clés uniques assurent l'idempotence.

## Modèle durable

Les migrations SQLite sont additives. Elles couvrent signaux, opportunités,
campagnes batch, propositions et produits associés, variantes créatives,
programmation, règles, préférences produit, réglages globaux, connexions sociales,
résultats sociaux et historique anti-fatigue. Les résultats analytics sont
nullable : aucune performance ne peut être fabriquée.

Le cockpit `/marketing` donne accès à la validation, aux opportunités, aux
programmations, au calendrier, à la bibliothèque et à une simulation explicable.
Les mutations sont authentifiées et protégées par CSRF comme le reste de DrCloud
OS. L'activation est une action explicite et réversible.

## Roadmap honnête

1. **Marketing Foundation + Autopilot** — cette PR.
2. **Creative AI** — implémenter les ports avec contrôle du Brand Kit et du packaging.
3. **Social Connections** — credential store et capacités vérifiées.
4. **Social Scheduler/Publisher** — seulement après conformité validée ; approval
   reste non contournable.
5. **Analytics** — ingestion de résultats réels.
6. **Sales-driven Marketing** — après disponibilité du Sales Ledger.
7. **Stock-driven Marketing** — après une source stock marketing qualifiée.
8. **Marketing Learning Loop** — règles/recommandations fondées sur historique,
   analytics et ventes, sans prétendre à un ML avant les données.

Creative AI réel, publication sociale, analytics live et recommandations ventes/
stock ne sont donc **pas terminés**. La prochaine étape recommandée est Creative
AI, précédée par la validation du Brand Kit officiel et de la policy conformité.
