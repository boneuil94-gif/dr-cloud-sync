# Marketing Automation Foundation

> Extension livrée : le Sales Ledger et les analytics déterministes sont décrits
> dans [SALES_LEDGER.md](SALES_LEDGER.md). L'autopilot sait désormais expliquer
> BEST_SELLER, SALES_SPIKE, SALES_DROP et TRENDING_PRODUCT à partir de ventes
> fraîches, et applique la fatigue issue de l'historique de publication. Aucune
> corrélation sociale/vente n'est transformée en attribution.

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

## Creative AI v1 livré

Le cockpit Marketing branche désormais les services provider-neutral existants sur
le workflow complet `DRAFT → génération → PREVIEW → review humaine → APPROVED /
REJECTED`. Le copy est structuré (headline, body, CTA, hashtags et texte légal),
et chaque proposition génère deux spécifications indépendantes : Instagram Story
9:16 et Instagram Post Square 1:1.

La génération lit uniquement l'identité commerciale canonique et le média
`ProductMedia PRIMARY`. Elle échoue si ce média manque et ne modifie jamais son
fichier. Le Brand Kit configurable (noir, blanc, vert Dr Cloud, direction moderne,
premium et propre) est inclus dans le fingerprint et visible dans la preview. La
policy `PRESERVE_ORIGINAL` est vérifiée en fail-closed avant la review.

Les endpoints Creative AI authentifiés et protégés par CSRF couvrent détail,
génération, régénération, approbation et refus motivé. Les audits
`CREATIVE_GENERATED`, `CREATIVE_REGENERATED`, `CREATIVE_APPROVED` et
`CREATIVE_REJECTED` conservent acteur, produits, créations, formats, fingerprint
quand applicable et policy sans secret ni binaire. L'approbation reste une action
humaine explicite et ne programme ni ne publie rien.

La baseline déterministe reste le fallback sans fournisseur : les cartes affichées
sont honnêtement nommées « spécification créative / preview » et ne prétendent pas
être des bitmaps générés.

## Roadmap honnête

1. **Marketing Foundation + Autopilot** — cette PR.
2. **Creative AI v1** — livré : génération provider-neutral, copy, Story/Square,
   PRIMARY, Brand Kit, PREVIEW, review humaine, approve/reject et audit.
3. **Social Connections** — credential store et capacités vérifiées.
4. **Social Scheduler/Publisher** — seulement après conformité validée ; approval
   reste non contournable.
5. **Analytics** — ingestion de résultats réels.
6. **Sales-driven Marketing** — après disponibilité du Sales Ledger.
7. **Stock-driven Marketing** — après une source stock marketing qualifiée.
8. **Marketing Learning Loop** — règles/recommandations fondées sur historique,
   analytics et ventes, sans prétendre à un ML avant les données.

Restent volontairement futurs : fournisseur génératif externe, fichiers images IA
finaux, Social Connections réelles, scheduler/publisher réel, analytics live,
Sales Ledger, marketing piloté par stock et learning loop. Aucun connecteur social
de ce module ne peut actuellement publier.
