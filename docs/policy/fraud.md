# fraud flags (warn/escalate)
Signals: debt_burden_extreme, zero_credit_history, employment_zero_with_large_loan.
Explain: cờ gian lận không tự REJECT mà escalate APPROVE→REVIEW (nodes.py decision).