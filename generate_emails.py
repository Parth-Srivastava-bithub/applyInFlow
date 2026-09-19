"""
Cold Email Generator using Groq API.
- Reads output/hr_gmail_posts.json
- For each HR contact, generates a personalized cold email based on their post
- Saves all generated emails to output/cold_emails.json
- Prints a preview of each email

Email drafting logic lives in pipeline/email_drafter.py.
The system prompt is in prompts/email_draft.txt — edit it there to iterate on tone/style.
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Dict

from dotenv import load_dotenv

load_dotenv()

from pipeline.email_drafter import draft_email
from axiom_logger import get_logger

axiom_logger = get_logger(service="email_generator")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not found in .env")
    sys.exit(1)

MODEL       = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
INPUT_JSON  = Path("output/hr_gmail_posts.json")
OUTPUT_JSON = Path("output/cold_emails.json")

# ── YOUR PROFILE ── edit this to match your background ──────────────────────
CANDIDATE_PROFILE = """
Candidate Profile:
- Name: Parth Srivastava
- Target Role: AI/ML Engineer
- Experience: 1+ years hands-on production experience
- Portfolio / Website: https://parthml.in
- Key Skills: Python, LangChain, LangGraph, RAG, AI Agents, LLM Fine-Tuning, QLoRA, PEFT, vLLM, FastAPI, Docker, RunPod, AWS, Apache Airflow, Prometheus, Locust
- Notable Projects:
  * Modeva: Multi-cloud GPU orchestrator managing live compute instances across RunPod & Novita with SSE streaming and AES-256-GCM encryption.
  * Real-Time Synchronized RAG: Dynamic document lifecycle (instant ingestion/deletion) with LangGraph decision trees.
  * Qwen2.5 Fine-Tuning: End-to-end QLoRA on RunPod GPUs with custom tokenization/label masking, served via vLLM inference API.
  * Apache Airflow Lakehouse: Bronze/Silver/Gold ETL pipeline processing 10K+ records/day.
- Location: India (Open to Remote / Hybrid / Immediate Joiner)
- Contact: parthsrivastava6112004@gmail.com | +91 8887664156 | https://linkedin.com/in/parth-srivastava-dsuser/
"""
CANDIDATE_NAME = "Parth Srivastava"

# Load extracted resume profile dynamically if available
STRUCTURED_RESUME_FILE = Path("output/structured_resume.json")
if STRUCTURED_RESUME_FILE.exists():
    try:
        from resume_parser import StructuredResumeProfile, format_candidate_context_for_prompt
        with open(STRUCTURED_RESUME_FILE, "r", encoding="utf-8") as f:
            _sr_data = json.load(f)
        _parsed = StructuredResumeProfile.model_validate(_sr_data)
        CANDIDATE_PROFILE = format_candidate_context_for_prompt(_parsed)
        if _parsed.name:
            CANDIDATE_NAME = _parsed.name
    except Exception as _e:
        pass


def dedupe_contacts(records: List[Dict]) -> List[Dict]:
    """Keep one record per unique email address."""
    seen_emails = set()
    unique = []
    for r in records:
        email = r.get("hr_email", "").lower()
        if email and email not in seen_emails:
            seen_emails.add(email)
            unique.append(r)
    return unique


def main():
    run_id = axiom_logger.new_run()
    axiom_logger.info("00_START", "Starting cold email generation batch", model=MODEL)

    if not INPUT_JSON.exists():
        err_msg = f"{INPUT_JSON} not found. Run run_posts.py first."
        axiom_logger.error("00_INPUT_MISSING", err_msg)
        print(f"ERROR: {err_msg}")
        sys.exit(1)

    with axiom_logger.step("01_LOAD_CONTACTS", description="Loading and deduplicating contacts from JSON") as meta:
        records = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
        contacts = dedupe_contacts(records)
        meta["total_records"] = len(records)
        meta["unique_contacts"] = len(contacts)

    print(f"\n{'='*60}")
    print(f"[*] Groq Cold Email Generator [Run ID: {run_id}]")
    print(f"    Model   : {MODEL}")
    print(f"    Contacts: {len(contacts)} unique HR emails")
    print(f"{'='*60}\n")

    results = []
    for i, contact in enumerate(contacts, 1):
        hr_name   = contact.get("name", "Hiring Manager")
        hr_title  = contact.get("title", "")
        hr_email  = contact.get("hr_email", "")
        post_text = contact.get("post_text", "")

        print(f"[{i}/{len(contacts)}] Generating email for: {hr_name} <{hr_email}>")

        try:
            with axiom_logger.step("02_DRAFT_EMAIL", description=f"Drafting email for {hr_email}", email=hr_email, name=hr_name) as meta:
                draft = draft_email(
                    hr_name=hr_name,
                    hr_title=hr_title,
                    hr_email=hr_email,
                    post_text=post_text,
                    candidate_context=CANDIDATE_PROFILE,
                    cand_name=CANDIDATE_NAME,
                    model=MODEL,
                )
                subject = draft["subject"]
                body    = draft["body"]
                meta["subject"] = subject
                meta["body_len"] = len(body)

                print(f"  Subject : {subject}")
                print(f"  Preview : {body[:120].replace(chr(10), ' ')}...")
                print()

                results.append({
                    "to_email":    hr_email,
                    "to_name":     draft.get("salutation") or hr_name or "",
                    "to_title":    hr_title,
                    "to_linkedin": contact.get("linkedin_url", ""),
                    "subject":     subject,
                    "body":        body,
                    "raw":         draft["raw"],
                    "query":       contact.get("query", ""),
                })
        except Exception as e:
            print(f"  ERROR: {e}\n")
            axiom_logger.error("02_DRAFT_EMAIL_ERROR", f"Error drafting email for {hr_email}: {e}", email=hr_email, error=str(e))
            results.append({
                "to_email": hr_email,
                "to_name":  hr_name,
                "error":    str(e),
            })

    with axiom_logger.step("03_SAVE_OUTPUT", description="Writing cold emails to output JSON", count=len(results)) as meta:
        OUTPUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        meta["file"] = str(OUTPUT_JSON)

    successful_count = len([r for r in results if "body" in r])
    axiom_logger.success(
        "04_BATCH_COMPLETE",
        f"Generated {successful_count}/{len(contacts)} cold emails successfully",
        successful=successful_count,
        total=len(contacts),
    )

    print(f"\n{'='*60}")
    print(f"Done! {successful_count} emails generated. [Run ID: {run_id}]")
    print(f"Saved to: {OUTPUT_JSON}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
