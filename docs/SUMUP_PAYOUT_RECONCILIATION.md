# Rapprochement SumUp transaction → payout

Une transaction rejoint un payout seulement par `payout_id`, item de payout ou code de transaction exact fourni par SumUp. Aucune composition n'est reconstruite par proximité de montant ou de date. Sans items, `composition=UNAVAILABLE`.

Le contrôle calcule `transactions - fees - refunds - chargebacks + adjustments = net payout`, avec `SETTLEMENT_ROUNDING_TOLERANCE` (0,01 EUR par défaut). Le résultat est `BALANCED`, `UNBALANCED` ou `UNAVAILABLE`; `PARTIAL` est réservé aux compositions explicitement partielles lorsqu'elles seront exposées par le provider. Les frais restent attachés à la transaction lorsqu'ils sont détaillés, sinon au payout ; aucune allocation proportionnelle n'est inventée.
