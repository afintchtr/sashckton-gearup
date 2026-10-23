"""
Deterministic fraud gate (proposal section 6.3) combined with the model score.

Any hit blocks a limit increase; hits marked (H) also trigger an immediate hold on the current limit.
Mapping to the proposal's table:
    Viya fraud score >= high-risk band (H)   -> model band == high_risk       (block increase, hold)
    Mule-account score present (H)           -> NOT built yet (gap). A hand-written pass-through rule was tried and REJECTED: on DEV data it flagged
                                                46% of normal customer-days at 58% mule recall. Mule typology is currently covered by the fraud score
                                                (mules are among its training positives); a dedicated mule-account model is a follow-up.
    New device first seen < 7 d              -> new_device_7 > 0              (block increase)
    Credential/phone/email change < 72 h     -> cred_change_3 > 0             (block increase)
    First-time payee on large amount         -> amount > 50% of daily cap     (block increase, step-up auth)
    Night velocity > 30% of cap 22:00-05:00  -> step-up auth + block increase   (proposal says (H) hold; changed after simulation: hold precision 2.8%)
    24 h outflow > 50% of balance, balance > Rp50 million -> step-up + block increase (proposal says (H) hold; precision 0.25%)
    Open fraud alert or dispute              -> any in last 30 d              (block increase)
"""

def evaluate(feats, today, prof, ctx, band):
    f = feats.iloc[0]
    cap = prof["cap"]
    hits = []

    def hit(code, text, hold=False, step_up=False, block=True):
        hits.append(dict(code=code, text=text, hold=hold, step_up=step_up, block_increase=block))

    if band == "high_risk":
        hit("FRAUD_SCORE_H", "model fraud score in high-risk band", hold=True)
    if f.new_device_7 > 0:
        hit("NEW_DEVICE", "device first seen in the last 7 days")
    if f.cred_change_3 > 0:
        hit("CREDENTIAL_CHANGE", "credential/phone/email changed in the last 72 hours")
    if today["ftp_large"] > 0:
        hit("FIRST_PAYEE_LARGE", "first-time payee with an amount above 50% of the daily cap", step_up=True)
    night_amt = today["night_outflow_amt"]
    if night_amt > 0.3 * cap:
        hit("NIGHT_VELOCITY", "more than 30% of the daily cap moved between 22:00 and 05:00", step_up=True)   # was (H) hold: precision only 2.8% in simulation
    bal_before = f.balance + today["outflow_amt"]
    if bal_before > 50e6 and today["outflow_amt"] > 0.5 * bal_before:
        hit("OUTFLOW_BALANCE", "24 h outflow above 50% of balance (balance > Rp50 million)", step_up=True)   # was (H) hold: precision 0.25% in simulation
    if ctx["alerts_30"] > 0 or ctx["disputes_30"] > 0:
        hit("OPEN_ALERT_OR_DISPUTE", "fraud alert or dispute in the last 30 days")
    return dict(hits=hits, codes=[h["code"] for h in hits], block_increase=any(h["block_increase"] for h in hits),
                hold=any(h["hold"] for h in hits), step_up=any(h["step_up"] for h in hits))
