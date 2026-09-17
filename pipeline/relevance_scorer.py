"""
pipeline/relevance_scorer.py — Isolated relevance-scoring stage.

Single public function: score_fit()

Input/Output contract:
    Input:  post_text (str), candidate_profile (dict), hr_title (str)
    Output: {
        "fit_score": int,          # 0-100
        "fit_tier":  str,          # "strong" | "moderate" | "poor"
        "dealbreaker": str|None,
        "matched_tags": list[str],
        "missing_tags":  list[str],
        "evaluated_fields_count": int,
        "extracted_requirements": dict
    }

Delegates entirely to scorer.score_match() — no logic duplication.
This wrapper exists to:
  1. Give the stage an explicit, stable import path.
  2. Document the contract so each stage can be tested independently.
  3. Allow scorer.py internals to be refactored without changing callers.
"""

from typing import Any, Dict


def score_fit(
    post_text: str,
    candidate_profile: Dict[str, Any],
    hr_title: str = "",
) -> Dict[str, Any]:
    """
    Score how well a candidate matches a LinkedIn job/recruiter post.

    Args:
        post_text:          Raw text of the LinkedIn post or job description.
        candidate_profile:  Candidate profile dict (name, role, experience,
                            skills, location keys used by scorer.py).
        hr_title:           Optional HR/recruiter headline for domain matching.

    Returns:
        Scoring result dict with fit_score, fit_tier, tags, etc.
        Always returns a dict (never raises) — on error, returns a safe default.
    """
    try:
        from scorer import score_match
        return score_match(
            post_text=post_text,
            candidate_profile=candidate_profile,
            hr_title=hr_title,
        )
    except Exception as exc:
        import logging
        logging.getLogger("autoapply.relevance_scorer").warning(
            f"score_fit failed: {exc}"
        )
        return {
            "fit_score": 70,
            "fit_tier": "moderate",
            "dealbreaker": None,
            "matched_tags": [],
            "missing_tags": [],
            "evaluated_fields_count": 0,
            "extracted_requirements": {},
        }
