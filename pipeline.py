"""
pipeline.py — Top-level AutoApply Pipeline Orchestrator

Shows the full offline pipeline flow in one place:

    stage 1: load_candidate_profile()
    stage 2: load_contacts()            <- db / hr_gmail_posts.json
    stage 3: score_fit()                <- pipeline/relevance_scorer.py
    stage 4: tailor_resume()            <- pipeline/resume_tailor.py
    stage 5: draft_email()             <- pipeline/email_drafter.py
    stage 6: write output/cold_emails.json

Run modes
---------
  python pipeline.py                        # full dry-run (no emails sent)
  python pipeline.py --limit 5             # process only 5 contacts
  python pipeline.py --skip-tailor          # skip LaTeX tailoring step
  python pipeline.py --min-score 75        # only process contacts scoring >=75

This script does NOT send any emails (use the Flask dashboard for that).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("autoapply.pipeline")

from axiom_logger import get_logger
axiom_logger = get_logger(service="pipeline")


# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR            = Path(__file__).resolve().parent
OUTPUT_DIR          = BASE_DIR / "output"
PROFILE_FILE        = OUTPUT_DIR / "profile.json"
STRUCTURED_RESUME   = OUTPUT_DIR / "structured_resume.json"
POSTS_JSON          = OUTPUT_DIR / "hr_gmail_posts.json"
COLD_EMAILS_OUTPUT  = OUTPUT_DIR / "cold_emails.json"
LATEX_SOURCE_FILE   = BASE_DIR / "resume.tex"
TAILORED_DIR        = BASE_DIR / "output" / "tailored_resumes"


# ── Stage helpers ─────────────────────────────────────────────────────────────

def load_candidate_profile() -> dict:
    """Stage 1: Load candidate profile and structured resume."""
    profile: dict = {}
    if PROFILE_FILE.exists():
        try:
            profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not load profile.json: {e}")

    # Merge structured resume fields if available
    if STRUCTURED_RESUME.exists():
        try:
            sr = json.loads(STRUCTURED_RESUME.read_text(encoding="utf-8"))
            if not profile.get("name") and sr.get("name"):
                profile["name"] = sr["name"]
            if not profile.get("role") and sr.get("headline_or_role"):
                profile["role"] = sr["headline_or_role"]
            if not profile.get("skills") and sr.get("key_skills"):
                profile["skills"] = ", ".join(sr["key_skills"][:15])
            if not profile.get("experience") and sr.get("years_of_experience"):
                profile["experience"] = f"{sr['years_of_experience']} experience"
        except Exception as e:
            logger.warning(f"Could not load structured_resume.json: {e}")

    logger.info(f"Candidate: {profile.get('name', 'Unknown')} | Role: {profile.get('role', 'N/A')}")
    return profile


def load_contacts(limit: int | None = None) -> list[dict]:
    """Stage 2: Load HR contacts from the scraped posts JSON."""
    if not POSTS_JSON.exists():
        logger.error(f"No contacts file found at {POSTS_JSON}. Run run_posts.py first.")
        sys.exit(1)
    contacts = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
    if limit:
        contacts = contacts[:limit]
    logger.info(f"Loaded {len(contacts)} contacts from {POSTS_JSON.name}")
    return contacts


def build_candidate_context(profile: dict) -> tuple[str, str]:
    """Build the candidate context string and display name from the profile."""
    cand_name = profile.get("name") or "Parth Srivastava"
    context = (
        f"Candidate Profile:\n"
        f"- Name: {cand_name}\n"
        f"- Role: {profile.get('role', 'AI/ML Engineer')}\n"
        f"- Experience: {profile.get('experience', '2+ years')}\n"
        f"- Skills: {profile.get('skills', 'Python, PyTorch, LangChain, FastAPI')}\n"
        f"- Location: {profile.get('location', 'India')}"
    )
    return cand_name, context


def load_base_tex() -> str | None:
    """Load master LaTeX resume source if it exists."""
    if LATEX_SOURCE_FILE.exists():
        return LATEX_SOURCE_FILE.read_text(encoding="utf-8")
    # Try looking for any .tex in the tailored dir
    for tex_f in TAILORED_DIR.glob("*.tex"):
        logger.info(f"Using fallback LaTeX source: {tex_f.name}")
        return tex_f.read_text(encoding="utf-8")
    return None


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    limit:       int | None = None,
    skip_tailor: bool       = False,
    min_score:   int        = 0,
    model:       str | None = None,
) -> list[dict]:
    """
    Run the full offline pipeline and return a list of drafted email dicts.

    Args:
        limit:       Max number of contacts to process.
        skip_tailor: If True, skips LaTeX resume tailoring for each contact.
        min_score:   Only process contacts whose fit_score >= this threshold.
        model:       LLM model override.

    Returns:
        List of result dicts (one per contact) with keys:
          to_email, to_name, fit_score, subject, body, tailored_tex (optional).
    """
    run_id = axiom_logger.new_run()
    axiom_logger.info("00_START", "Starting AutoApply pipeline run", limit=limit, skip_tailor=skip_tailor, min_score=min_score)

    print("\n" + "=" * 60)
    print(f"  AutoApply Pipeline — Offline Dry-Run [Run ID: {run_id}]")
    print("=" * 60)

    # Stage 1
    print("\n[Stage 1] Loading candidate profile...")
    with axiom_logger.step("01_LOAD_PROFILE", description="Loading candidate profile and structured resume") as step_meta:
        profile = load_candidate_profile()
        cand_name, candidate_context = build_candidate_context(profile)
        base_tex = None if skip_tailor else load_base_tex()
        step_meta["candidate_name"] = cand_name
        step_meta["has_base_tex"] = bool(base_tex)

    # Stage 2
    print("[Stage 2] Loading HR contacts...")
    with axiom_logger.step("02_LOAD_CONTACTS", description="Loading HR contacts from posts file", limit=limit) as step_meta:
        contacts = load_contacts(limit=limit)
        step_meta["contacts_count"] = len(contacts)

    # Pipeline stage imports
    from pipeline.relevance_scorer import score_fit
    from pipeline.email_drafter    import draft_email
    if not skip_tailor and base_tex:
        from pipeline.resume_tailor import tailor_resume
    else:
        tailor_resume = None  # type: ignore

    results = []

    for i, contact in enumerate(contacts, 1):
        hr_name  = contact.get("name")  or "Hiring Manager"
        hr_title = contact.get("title") or ""
        hr_email = contact.get("hr_email") or contact.get("email") or contact.get("gmail") or ""
        post_text = contact.get("post_text") or ""

        if not hr_email:
            logger.debug(f"  Skipping contact #{i}: no email address.")
            axiom_logger.warn("02_CONTACT_SKIP", f"Skipping contact #{i}: no email address", index=i, name=hr_name)
            continue

        print(f"\n[{i}/{len(contacts)}] {hr_name} <{hr_email}>")

        # ── Stage 3: Score relevance ──────────────────────────────────────────
        print("  [Stage 3] Scoring relevance...")
        try:
            with axiom_logger.step("03_SCORE_FIT", description=f"Scoring relevance for {hr_email}", email=hr_email, contact_num=i) as step_meta:
                fit = score_fit(post_text, profile, hr_title)
                fit_score = fit.get("fit_score", 0)
                fit_tier  = fit.get("fit_tier", "unknown")
                step_meta["fit_score"] = fit_score
                step_meta["fit_tier"] = fit_tier
                print(f"  → fit_score={fit_score}  tier={fit_tier}")
        except Exception as e:
            logger.warning(f"  Scoring failed for {hr_email}: {e}")
            fit_score = 0
            fit_tier = "error"

        if fit_score < min_score:
            print(f"  ⚠ Score {fit_score} < threshold {min_score} — skipping.")
            axiom_logger.info("03_SCORE_FILTERED", f"Contact {hr_email} fit score {fit_score} below threshold {min_score}", email=hr_email, score=fit_score)
            continue

        result: dict = {
            "to_email":  hr_email,
            "to_name":   hr_name,
            "to_title":  hr_title,
            "fit_score": fit_score,
            "fit_tier":  fit_tier,
        }

        # ── Stage 4: Tailor resume ────────────────────────────────────────────
        if tailor_resume and base_tex:
            print("  [Stage 4] Tailoring resume...")
            try:
                with axiom_logger.step("04_TAILOR_RESUME", description=f"Tailoring LaTeX resume for {hr_email}", email=hr_email) as step_meta:
                    tailored_tex = tailor_resume(
                        base_tex=base_tex,
                        post_text=post_text,
                        hr_title=hr_title,
                        to_email=hr_email,
                        feedback="",
                        cand_name=cand_name,
                        model=model,
                    )
                    result["tailored_tex"] = tailored_tex
                    step_meta["tex_length"] = len(tailored_tex)
                    print(f"  → Tailored LaTeX: {len(tailored_tex)} chars")
            except Exception as e:
                logger.warning(f"  Resume tailoring failed: {e}")
                axiom_logger.warn("04_TAILOR_RESUME_FAILED", f"Resume tailoring failed for {hr_email}: {e}", email=hr_email, error=str(e))
        else:
            print("  [Stage 4] Resume tailoring skipped.")

        # ── Stage 5: Draft email ──────────────────────────────────────────────
        print("  [Stage 5] Drafting email...")
        try:
            with axiom_logger.step("05_DRAFT_EMAIL", description=f"Drafting cold email for {hr_email}", email=hr_email) as step_meta:
                draft = draft_email(
                    hr_name=hr_name,
                    hr_title=hr_title,
                    hr_email=hr_email,
                    post_text=post_text,
                    candidate_context=candidate_context,
                    cand_name=cand_name,
                    model=model,
                )
                result["subject"] = draft["subject"]
                result["body"]    = draft["body"]
                step_meta["subject"] = draft["subject"]
                step_meta["body_len"] = len(draft["body"])
                print(f"  → Subject: {draft['subject']}")
                print(f"  → Preview: {draft['body'][:100].replace(chr(10), ' ')}...")
        except Exception as e:
            logger.warning(f"  Email drafting failed: {e}")
            result["error"] = str(e)
            axiom_logger.error("05_DRAFT_EMAIL_FAILED", f"Email drafting failed for {hr_email}: {e}", email=hr_email, error=str(e))

        results.append(result)

    # ── Stage 6: Write output ─────────────────────────────────────────────────
    print(f"\n[Stage 6] Writing {len(results)} results to {COLD_EMAILS_OUTPUT} ...")
    with axiom_logger.step("06_WRITE_OUTPUT", description="Writing generated drafts to JSON file", drafted_count=len(results)) as step_meta:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        COLD_EMAILS_OUTPUT.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        step_meta["file"] = str(COLD_EMAILS_OUTPUT)

    axiom_logger.success(
        "07_PIPELINE_COMPLETE",
        f"Pipeline run completed successfully. Drafted {len(results)} emails.",
        total_drafted=len(results),
        min_score=min_score,
    )

    print("\n" + "=" * 60)
    print(f"  Done! {len(results)} emails drafted. [Run ID: {run_id}]")
    if min_score:
        print(f"  (Only contacts with fit_score >= {min_score} were processed.)")
    print(f"  Output: {COLD_EMAILS_OUTPUT}")
    print("=" * 60 + "\n")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AutoApply Pipeline — offline dry-run (no emails sent).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--limit", "-n", type=int, default=None,
        help="Process only the first N contacts."
    )
    parser.add_argument(
        "--skip-tailor", action="store_true",
        help="Skip LaTeX resume tailoring step (faster)."
    )
    parser.add_argument(
        "--min-score", type=int, default=0,
        help="Only process contacts with fit_score >= this value."
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help=f"Override LLM model (default: {os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')})."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_pipeline(
        limit       = args.limit,
        skip_tailor = args.skip_tailor,
        min_score   = args.min_score,
        model       = args.model,
    )
