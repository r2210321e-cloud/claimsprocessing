"""
ai_service.py
─────────────────────────────────────────────────────────────────────────────
Google Gemini integration for automatic claim damage assessment.

FREE — no payment required. Uses gemini-1.5-flash via Google AI Studio.
Get your free API key at: https://aistudio.google.com/app/apikey
─────────────────────────────────────────────────────────────────────────────
"""

import base64
import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL   = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".webp"}
SEVERITY_CHOICES = ["MINOR", "MODERATE", "SEVERE", "TOTAL_LOSS"]

PROMPT = """You are an expert motor vehicle insurance claims assessor for Zimnat Insurance in Zimbabwe.
Analyse the claim details and any vehicle damage photographs provided.

Respond ONLY with valid JSON — no markdown fences, no extra text before or after.
Use this exact schema:
{
  "damage_description": "<2-4 sentence plain-English description of the damage>",
  "damaged_parts": [
    {"part": "<component name>", "severity": "Minor|Moderate|Severe", "description": "<what is damaged>", "cost_usd": <number>}
  ],
  "damage_severity": "MINOR|MODERATE|SEVERE|TOTAL_LOSS",
  "parts_cost_usd": <number>,
  "labour_cost_usd": <number>,
  "paint_cost_usd": <number>,
  "other_costs_usd": <number>,
  "total_estimate_usd": <number>,
  "fraud_score": <0.0 to 1.0>,
  "fraud_notes": "<brief reasoning>",
  "recommended_workshop": "<workshop type e.g. Authorised Toyota dealer>",
  "assessor_notes": "<caveats or next steps>"
}

Severity: MINOR=cosmetic/<USD2000, MODERATE=structural/USD2000-8000, SEVERE=major/USD8000-15000, TOTAL_LOSS=>USD15000
Use Zimbabwe market rates. Fraud: 0.0-0.3 low, 0.3-0.6 medium, 0.6-1.0 high risk."""


def run_ai_assessment(claim) -> bool:
    from .models import ClaimAssessment
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is not set — skipping AI assessment")
            return False

        parts = [{"text": _build_text_context(claim) + "\n\n" + PROMPT}]

        images_added = 0
        try:
            damage_docs = claim.documents.filter(
                document_type__in=["ACCIDENT_PHOTO", "VEHICLE_PHOTO"]
            ).order_by("-created_at")[:5]
            for doc in damage_docs:
                img_part = _encode_image(doc)
                if img_part:
                    parts.append(img_part)
                    images_added += 1
        except Exception as e:
            logger.warning(f"Could not load images for {claim.claim_number}: {e}")

        if images_added == 0:
            parts[0]["text"] += "\n\nNote: No damage photos provided. Base assessment on incident description only."

        raw_text = _call_gemini(api_key, parts)
        data = json.loads(raw_text.replace("```json", "").replace("```", "").strip())

        severity = data.get("damage_severity", "MINOR")
        if severity not in SEVERITY_CHOICES:
            severity = "MINOR"

        total_estimate = float(data.get("total_estimate_usd") or 0)
        fraud_score    = max(0.0, min(1.0, float(data.get("fraud_score") or 0.0)))

        claim.ai_damage_summary   = data.get("damage_description", "")
        claim.ai_estimated_repair = total_estimate
        claim.ai_fraud_score      = fraud_score
        claim.save(update_fields=["ai_damage_summary", "ai_estimated_repair", "ai_fraud_score"])

        claim.assessments.filter(assessment_type=ClaimAssessment.AssessmentType.AI_INITIAL).delete()

        assessment = ClaimAssessment.objects.create(
            claim                = claim,
            assessor             = None,
            assessment_type      = ClaimAssessment.AssessmentType.AI_INITIAL,
            damage_description   = data.get("damage_description", ""),
            damage_severity      = severity,
            damaged_parts        = [p if isinstance(p, dict) else {"part": p, "severity": "MINOR", "description": "", "cost_usd": 0} for p in data.get("damaged_parts", [])],
            parts_cost           = float(data.get("parts_cost_usd") or 0),
            labour_cost          = float(data.get("labour_cost_usd") or 0),
            paint_cost           = float(data.get("paint_cost_usd") or 0),
            other_costs          = float(data.get("other_costs_usd") or 0),
            total_estimate       = total_estimate,
            recommended_workshop = data.get("recommended_workshop", ""),
            notes                = f"{data.get('assessor_notes', '')}\n\nFraud notes: {data.get('fraud_notes', '')}".strip(),
            is_final             = False,
        )
        assessment.calculate_total()
        assessment.save(update_fields=["total_estimate"])
        logger.info(f"Gemini assessment complete for {claim.claim_number}: severity={severity}, USD {total_estimate:.2f}")
        return True

    except json.JSONDecodeError as e:
        logger.error(f"Gemini JSON parse error for {claim.claim_number}: {e}")
        return False
    except Exception as e:
        logger.error(f"Gemini error for {claim.claim_number}: {type(e).__name__}: {e}", exc_info=True)
        return False


def _call_gemini(api_key: str, parts: list) -> str:
    url  = GEMINI_URL.format(model=GEMINI_MODEL, key=api_key)
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["candidates"][0]["content"]["parts"][0]["text"]


def _build_text_context(claim) -> str:
    v, p = claim.vehicle, claim.policy
    lines = [
        "=== CLAIM ===",
        f"Type: {claim.get_incident_type_display()} | Date: {claim.incident_date} | Location: {claim.incident_location}",
        f"Fault: {claim.get_fault_type_display()} | Police report: {'Yes' if claim.police_report_filed else 'No'}",
        f"Description: {claim.incident_description or 'None provided'}",
        "",
        "=== VEHICLE ===",
        f"{v.year} {v.make} {v.model} | {v.get_body_type_display()} | {v.color} | Plate: {v.license_plate}",
        "",
        "=== POLICY ===",
        f"Cover: {p.get_cover_type_display()} | Sum insured: USD {p.sum_insured:,.2f} | Excess: USD {p.excess_amount:,.2f}",
    ]
    if claim.claimed_amount:
        lines.append(f"Claimant estimate: USD {claim.claimed_amount:,.2f}")
    return "\n".join(lines)


def _encode_image(doc) -> dict | None:
    try:
        if not doc.file:
            return None
        ext = os.path.splitext((doc.file_name or "").lower())[1]
        if ext not in IMAGE_EXTS:
            return None
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
        doc.file.open("rb")
        data = base64.standard_b64encode(doc.file.read()).decode("utf-8")
        doc.file.close()
        return {"inline_data": {"mime_type": mime, "data": data}}
    except Exception as e:
        logger.warning(f"Could not encode image {doc.id}: {e}")
        return None
