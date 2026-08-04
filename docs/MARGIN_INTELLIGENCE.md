# Purchase / Margin Intelligence

Les coûts confirmés FIFO et le chiffre d'affaires du Sales Ledger alimentent une
marge brute et un pourcentage uniquement lorsque la couverture est complète. Le
score conserve séparément sales, stock, margin, social et risk_penalty.

Aucune remise chiffrée n'est générée : tant que coût d'achat, marge minimale (20 %),
prix plancher et contraintes commerciales ne sont pas tous vérifiables, seule une
mise en avant non tarifaire est proposée. Une marge inconnue demeure `null`.
