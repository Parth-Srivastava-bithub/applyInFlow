"""
Cold Email Generator using Groq API (openai/gpt-oss-120b)
- Reads hr_gmail_posts.json
- For each HR contact, generates a personalized cold email based on their post
- Saves all generated emails to output/cold_emails.json
- Prints a preview of each email
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not found in .env")
    sys.exit(1)

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
MODEL      = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
INPUT_JSON = Path("output/hr_gmail_posts.json")
OUTPUT_JSON = Path("output/cold_emails.json")

# ── YOUR PROFILE ── edit this to match your background ──────────────────────
CANDIDATE_PROFILE = """
Name: [Your Name]
Role: AI/ML Engineer
Experience: 2+ years building ML pipelines, LLM apps, RAG systems
Skills: Python, PyTorch, TensorFlow, LangChain, FastAPI, Docker, AWS
Education: B.Tech / B.E. in Computer Science (or related)
Location: India (open to remote / hybrid / relocation)
LinkedIn: [Your LinkedIn URL]
Phone: [Your Phone]
"""
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are an expert job application email writer.
You write short, professional, personalized cold emails from a job seeker to an HR recruiter.

Candidate Profile:
{CANDIDATE_PROFILE}

Rules:
- Keep email under 150 words
- Be direct and confident, not desperate
- Reference the specific role/company from the recruiter's post
- End with a clear CTA (attached resume, available for call)
- Use plain text, no markdown, no emojis
- Subject line format: "Application – [Role] | [Candidate Name]"
- Output ONLY: Subject: <subject>\\n\\n<body>
"""


def clean_post_text(text: str) -> str:
    """Strip UI noise from scraped post text."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # Drop pure nav/UI lines
    skip = {'Feed post', 'Follow', 'Like', 'Comment', 'Repost', 'Send', '3rd+', '•'}
    lines = [l for l in lines if l not in skip and not re.match(r'^\d+\s*(reaction|comment|repost)', l, re.I)]
    return '\n'.join(lines[:25])  # Keep first 25 meaningful lines


def groq_generate(hr_name: str, hr_title: str, post_text: str) -> str:
    """Call Groq API (streaming) and return the full generated email."""
    user_msg = (
        f"HR Name: {hr_name}\n"
        f"HR Title: {hr_title}\n\n"
        f"Their LinkedIn post (job they're hiring for):\n{clean_post_text(post_text)}\n\n"
        "Write a cold email from the candidate to this HR person applying for the role."
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 1,
        "max_completion_tokens": 2048,
        "top_p": 1,
        "stream": True,
        "reasoning_effort": "medium",
        "stop": None,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    response = requests.post(GROQ_URL, json=payload, headers=headers, stream=True, timeout=60)
    response.raise_for_status()

    # Collect streamed chunks
    full_text = ""
    for line in response.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if line.startswith("data: "):
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    full_text += content
            except json.JSONDecodeError:
                continue

    return full_text.strip()


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
    if not INPUT_JSON.exists():
        print(f"ERROR: {INPUT_JSON} not found. Run run_posts.py first.")
        sys.exit(1)

    records = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    contacts = dedupe_contacts(records)

    print(f"\n{'='*60}")
    print(f"[*] Groq Cold Email Generator")
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
            email_text = groq_generate(hr_name, hr_title, post_text)

            # Parse subject and body
            subject = ""
            body = email_text
            if email_text.lower().startswith("subject:"):
                lines = email_text.split('\n', 2)
                subject = lines[0].replace("Subject:", "").replace("subject:", "").strip()
                body = '\n'.join(lines[1:]).strip()

            print(f"  Subject : {subject}")
            print(f"  Preview : {body[:120].replace(chr(10), ' ')}...")
            print()

            results.append({
                "to_email":  hr_email,
                "to_name":   hr_name,
                "to_title":  hr_title,
                "to_linkedin": contact.get("linkedin_url", ""),
                "subject":   subject,
                "body":      body,
                "raw":       email_text,
                "query":     contact.get("query", ""),
            })
        except Exception as e:
            print(f"  ERROR: {e}\n")
            results.append({
                "to_email": hr_email,
                "to_name":  hr_name,
                "error":    str(e),
            })

    OUTPUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"Done! {len([r for r in results if 'body' in r])} emails generated.")
    print(f"Saved to: {OUTPUT_JSON}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
