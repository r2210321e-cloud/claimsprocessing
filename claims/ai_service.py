"""
ai_service.py
─────────────────────────────────────────────────────────────────────────────
Google Gemini integration for automatic claim damage assessment.
"""

import base64
import json
import logging
import os
import urllib.request
import urllib.error
import time
import random
import hashlib

from django.core.cache import cache

logger = logging.getLogger(__name__)

GEMINI_MODEL = "models/gemini-flash-latest"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"
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
  "recommended_workshop": "<workshop type>",
  "assessor_notes": "<caveats or next steps>"
}
"""


# -------------------------
# HASH FOR CACHE
# -------------------------
def _hash_parts(parts):
    raw = json.dumps(parts, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

def _image_only_cache_key(image_parts: list) -> str:
    """
    Generates a cache key based ONLY on the image data (no prompt, no claim text).
    This allows run_ai_assessment to find the result from analyze_images.
    """
    raw = json.dumps(image_parts, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"ai:images_only:{digest}"


def analyze_images(images: list) -> dict:
    """
    Used by AIAssessmentProxyView (pre-claim submission).
    Accepts raw base64 images from frontend.
    Returns structured AI response.
    """

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise Exception("GEMINI_API_KEY not set")

    image_parts = []

    # Limit to 3 images (same as claim flow)
    for img in images[:3]:
        try:
            src = img.get("source", {})
            data = src.get("data")

            if not data:
                continue

            # Validate base64
            base64.b64decode(data)

            image_parts.append({
                "inline_data": {
                    "mime_type": src.get("media_type", "image/jpeg"),
                    "data": data,
                }
            })

        except Exception:
            continue

    if not image_parts:
        raise ValueError("No valid images provided")

    # -------------------------
    # CACHE — check image-only key first
    # -------------------------
    cache_key = _image_only_cache_key(image_parts)
    cached = cache.get(cache_key)

    if cached:
        return cached

    # Add prompt LAST (important for Gemini)
    parts = image_parts + [{"text": PROMPT}]

    # -------------------------
    # CALL GEMINI
    # -------------------------
    raw_text = _call_gemini(api_key, parts)
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    import re
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)

    if not match:
        raise ValueError("No JSON found in AI response")

    json_str = match.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Bad AI JSON: {raw_text}")
        raise ValueError("AI returned invalid JSON")

    # Store with a 30-minute timeout so it's still available when the claim is submitted
    cache.set(cache_key, data, timeout=1800)

    return data
# -------------------------
# MAIN ENTRY
# -------------------------
def run_ai_assessment(claim) -> bool:
    from .models import ClaimAssessment

    # 🚨 Prevent duplicate runs
    if claim.ai_damage_summary:
        return False

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY not set")
            return False

        # ✅ Encode images first (limit to 3)
        image_parts = []
        damage_docs = claim.documents.filter(
            document_type__in=["ACCIDENT_PHOTO", "VEHICLE_PHOTO"]
        ).order_by("-created_at")[:3]

        for doc in damage_docs:
            img_part = _encode_image(doc)
            if img_part:
                image_parts.append(img_part)

        # -------------------------
        # CACHE — reuse analyze_images result if available
        # If the user previewed the assessment before submitting,
        # the result is already cached under the image-only key.
        # Reuse it to keep the estimate consistent.
        # -------------------------
        data = None
        if image_parts:
            preview_cache_key = _image_only_cache_key(image_parts)
            data = cache.get(preview_cache_key)
            if data:
                logger.info(f"Reusing pre-submission AI result for {claim.claim_number}")

        if not data:
            # No cached preview found — call Gemini fresh
            parts = [{"text": _build_text_context(claim) + "\n\n" + PROMPT}]
            parts.extend(image_parts)

            if not image_parts:
                parts[0]["text"] += "\n\nNo damage photos provided."

            raw_text = _call_gemini(api_key, parts)
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw_text)

        # -------------------------
        # PROCESS RESPONSE
        # -------------------------
        severity = data.get("damage_severity", "MINOR")
        if severity not in SEVERITY_CHOICES:
            severity = "MINOR"

        total_estimate = float(data.get("total_estimate_usd") or 0)
        fraud_score    = max(0.0, min(1.0, float(data.get("fraud_score") or 0.0)))

        claim.ai_damage_summary   = data.get("damage_description", "")
        claim.ai_estimated_repair = total_estimate
        claim.ai_fraud_score      = fraud_score

        claim.save(update_fields=[
            "ai_damage_summary",
            "ai_estimated_repair",
            "ai_fraud_score"
        ])

        # Remove previous AI assessments
        claim.assessments.filter(
            assessment_type=ClaimAssessment.AssessmentType.AI_INITIAL
        ).delete()

        assessment = ClaimAssessment.objects.create(
            claim=claim,
            assessor=None,
            assessment_type=ClaimAssessment.AssessmentType.AI_INITIAL,
            damage_description=data.get("damage_description", ""),
            damage_severity=severity,
            damaged_parts=data.get("damaged_parts", []),
            parts_cost=float(data.get("parts_cost_usd") or 0),
            labour_cost=float(data.get("labour_cost_usd") or 0),
            paint_cost=float(data.get("paint_cost_usd") or 0),
            other_costs=float(data.get("other_costs_usd") or 0),
            total_estimate=total_estimate,
            recommended_workshop=data.get("recommended_workshop", ""),
            notes=f"{data.get('assessor_notes', '')}\n\nFraud: {data.get('fraud_notes', '')}",
            is_final=False,
        )

        assessment.calculate_total()
        assessment.save(update_fields=["total_estimate"])

        logger.info(f"AI assessment complete for {claim.claim_number}")
        return True

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return False

    except Exception as e:
        logger.error(f"AI error: {type(e).__name__}: {e}", exc_info=True)
        return False


# -------------------------
# GEMINI CALL (FIXED)
# -------------------------
def _call_gemini(api_key: str, parts: list) -> str:
    import json, urllib.request, urllib.error, time, random

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"

    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "response_mime_type": "application/json"
        }
    }).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": api_key
                },
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                response_body = resp.read().decode("utf-8")

            print("\n========== GEMINI RAW RESPONSE ==========")
            print(response_body)
            print("========================================\n")

            result = json.loads(response_body)

            # Debug structure
            print("\n========== PARSED STRUCTURE ==========")
            print(result)
            print("=====================================\n")

            # Safe extraction
            candidates = result.get("candidates")
            if not candidates:
                raise Exception(f"No candidates in response: {result}")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])

            if not parts:
                raise Exception(f"No parts in response: {result}")

            text = parts[0].get("text")

            if not text:
                raise Exception(f"No text in response: {result}")

            print("\n========== EXTRACTED TEXT ==========")
            print(text)
            print("===================================\n")

            return text
            

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()
            except:
                pass

            print("GEMINI ERROR:", e.code, error_body)

            if e.code == 429:
                time.sleep((attempt + 1) * 5)
                continue

            raise

    raise Exception("Gemini failed after retries")
# -------------------------
# HELPERS
# -------------------------
def _build_text_context(claim) -> str:
    v, p = claim.vehicle, claim.policy

    lines = [
        "=== CLAIM ===",
        f"{claim.get_incident_type_display()} | {claim.incident_date} | {claim.incident_location}",
        f"Fault: {claim.get_fault_type_display()}",
        f"{claim.incident_description or 'No description'}",
        "",
        "=== VEHICLE ===",
        f"{v.year} {v.make} {v.model} | {v.color} | {v.license_plate}",
        "",
        "=== POLICY ===",
        f"Cover: {p.get_cover_type_display()} | Sum: {p.sum_insured} | Excess: {p.excess_amount}",
    ]

    return "\n".join(lines)


def _encode_image(doc):
    try:
        if not doc.file:
            return None

        ext = os.path.splitext((doc.file_name or "").lower())[1]
        if ext not in IMAGE_EXTS:
            return None

        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp"
        }.get(ext, "image/jpeg")

        doc.file.open("rb")
        data = base64.b64encode(doc.file.read()).decode()
        doc.file.close()

        return {"inline_data": {"mime_type": mime, "data": data}}

    except Exception as e:
        logger.warning(f"Image encode failed: {e}")
        return None