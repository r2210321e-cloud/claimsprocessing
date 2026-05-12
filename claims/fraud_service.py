"""
fraud_service.py
────────────────
Rule-based fraud scoring for insurance claims.
Returns a score between 0.0 (no risk) and 1.0 (high risk).

Called automatically when a claim is submitted.
Result is saved to Claim.ai_fraud_score.

Rules applied:
  1. Policy age           — claim filed very soon after policy started
  2. Claim vs sum insured — claimed amount is close to or exceeds sum insured
  3. No police report     — collision or theft with no police report filed
  4. Third party gap      — third party involved but no name/plate provided
  5. Repeat claims        — client has 2+ claims in the past 12 months
  6. Fault + no police    — "Not at fault" but no police report to back it up
  7. High claimed amount  — absolute amount exceeds high-value threshold
"""

import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

HIGH_AMOUNT_THRESHOLD_USD = 50_000


def calculate_fraud_score(claim) -> float:
    """
    Evaluate a Claim instance against all fraud rules and return a
    normalised score in [0.0, 1.0]. Saves the result to claim.ai_fraud_score.
    """
    score = 0.0
    flags = []

    # ── Rule 1: Policy age ────────────────────────────────────────────────────
    try:
        policy_age = (claim.incident_date - claim.policy.start_date).days
        if policy_age <= 30:
            weight = 0.20
            score += weight
            flags.append(f'Policy only {policy_age} day(s) old at incident (+{weight})')
    except Exception:
        pass

    # ── Rule 2: Claimed amount vs sum insured ─────────────────────────────────
    try:
        if claim.claimed_amount and claim.policy.sum_insured:
            ratio = float(claim.claimed_amount) / float(claim.policy.sum_insured)
            if ratio >= 0.80:
                weight = round(min(0.20, ratio * 0.15), 3)
                score += weight
                flags.append(f'Claimed {ratio*100:.0f}% of sum insured (+{weight})')
    except Exception:
        pass

    # ── Rule 3: No police report on collision / theft ─────────────────────────
    try:
        if claim.incident_type in ('COLLISION', 'THEFT') and not claim.police_report_filed:
            weight = 0.15
            score += weight
            flags.append(f'{claim.incident_type} with no police report (+{weight})')
    except Exception:
        pass

    # ── Rule 4: Third party involved but details missing ──────────────────────
    try:
        if claim.third_party_involved:
            has_name  = bool((claim.third_party_name or '').strip())
            has_plate = bool((claim.third_party_license_plate or '').strip())
            if not has_name and not has_plate:
                weight = 0.15
                score += weight
                flags.append(f'Third party ticked but no name/plate provided (+{weight})')
    except Exception:
        pass

    # ── Rule 5: Repeat claims in the past 12 months ───────────────────────────
    try:
        from .models import Claim as ClaimModel
        one_year_ago = timezone.now().date() - timezone.timedelta(days=365)
        recent_count = (
            ClaimModel.objects
            .filter(client=claim.client, submitted_at__date__gte=one_year_ago)
            .exclude(pk=claim.pk)
            .count()
        )
        if recent_count >= 2:
            weight = round(min(0.20, 0.10 * recent_count), 3)
            score += weight
            flags.append(f'Client has {recent_count} other claim(s) in past 12 months (+{weight})')
    except Exception:
        pass

    # ── Rule 6: Not-at-fault but no police report ─────────────────────────────
    try:
        if claim.fault_type == 'NOT_AT_FAULT' and not claim.police_report_filed:
            weight = 0.10
            score += weight
            flags.append(f'Not-at-fault claimed but no police report (+{weight})')
    except Exception:
        pass

    # ── Rule 7: Unusually high absolute claimed amount ────────────────────────
    try:
        if claim.claimed_amount and float(claim.claimed_amount) >= HIGH_AMOUNT_THRESHOLD_USD:
            weight = 0.10
            score += weight
            flags.append(f'Claimed amount USD {float(claim.claimed_amount):,.0f} exceeds threshold (+{weight})')
    except Exception:
        pass

    # ── Normalise to [0.0, 1.0] and save ─────────────────────────────────────
    final_score = round(min(score, 1.0), 3)

    try:
        claim.ai_fraud_score = final_score
        claim.save(update_fields=['ai_fraud_score'])
    except Exception:
        pass

    if flags:
        logger.info(
            'Fraud score for %s: %.3f | %s',
            claim.claim_number, final_score, ' | '.join(flags)
        )

    return final_score
