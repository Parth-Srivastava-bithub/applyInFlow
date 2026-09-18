"""
Resume Parser Middleware with Pydantic BaseModel validation & Groq AI structuring.
Extracts high-fidelity raw text & hyperlink annotations from PDF/DOCX/TXT/TEX resumes,
and structures them accurately into a validated Pydantic model with AI and robust heuristic fallback.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

import pypdf
import requests
from docx import Document
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger("autoapply.resume_parser")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


# ── Pydantic Schema for Structured Resume ────────────────────────────────────

class StructuredResumeProfile(BaseModel):
    name: str = Field(..., description="Candidate's full name")
    email: Optional[str] = Field(None, description="Candidate email address")
    phone: Optional[str] = Field(None, description="Phone number with country code")
    location: Optional[str] = Field(None, description="City, State, Country location (e.g. 'Ghaziabad, UP, India' or 'San Francisco, CA')")
    linkedin_url: Optional[str] = Field(None, description="Full LinkedIn profile URL (e.g. https://www.linkedin.com/in/...)")
    github_or_portfolio: Optional[str] = Field(None, description="GitHub profile or portfolio website URL")
    
    headline_or_role: str = Field(..., description="Candidate's target role or professional headline (e.g. AI / ML Engineer)")
    years_of_experience: Optional[str] = Field(None, description="Estimated total years of relevant experience (e.g. '2+ years' or '1.5 years')")
    professional_summary: str = Field(..., description="Compelling 2-3 sentence executive summary of background, technical capabilities, and key strengths")
    
    key_skills: List[str] = Field(
        default_factory=list,
        description="Comprehensive list of technical skills, programming languages, specific AI/LLM models (e.g. Qwen2.5, LLaMA), frameworks, databases, and tools"
    )
    key_projects_or_achievements: List[str] = Field(
        default_factory=list,
        description="Top 3-6 high-impact projects, quantifiable achievements, or work accomplishments with metrics"
    )
    education: Optional[str] = Field(None, description="Highest degree, institution name, location, CGPA/marks, and graduation years")
    links: List[str] = Field(default_factory=list, description="All extracted hyperlinks (LinkedIn, GitHub, Portfolios, Demos)")


# ── Unicode & Text Normalization ─────────────────────────────────────────────

def _clean_unicode_characters(text: str) -> str:
    """Normalize non-breaking hyphens, special dashes, smart quotes, and strange symbols to clean ASCII."""
    if not text:
        return ""
    # Hyphens, en-dashes, em-dashes, minus signs
    for ch in ['\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015', '\u2212']:
        text = text.replace(ch, '-')
    # Smart quotes
    text = text.replace('\u2018', "'").replace('\u2019', "'").replace('`', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    # Bullets
    for b in ['\u2022', '\u2023', '\u25e6', '\u25aa', '\u25ab', '\u25cf']:
        text = text.replace(b, '* ')
    # Non-breaking spaces and tabs
    text = text.replace('\u00a0', ' ').replace('\u200b', '')
    return text


# ── Text & Hyperlink Extractor ───────────────────────────────────────────────

def extract_text_from_file(file_path: Path) -> str:
    """
    Extract raw text and embedded hyperlink annotations from PDF, DOCX, TXT, or TEX.
    Recovers clickable URLs from PDF annotations that plain text extraction typically misses.
    """
    ext = file_path.suffix.lower()
    text = ""
    discovered_links: List[str] = []
    
    if ext == ".pdf":
        try:
            reader = pypdf.PdfReader(str(file_path))
            for page in reader.pages:
                # Extract text
                t = page.extract_text()
                if t:
                    text += t + "\n"
                # Extract embedded link annotations (/Annots -> /A -> /URI)
                if '/Annots' in page:
                    for annot in page['/Annots']:
                        try:
                            obj = annot.get_object()
                            if '/A' in obj and '/URI' in obj['/A']:
                                uri = str(obj['/A']['/URI']).strip()
                                if uri and uri not in discovered_links:
                                    discovered_links.append(uri)
                        except Exception:
                            pass
        except Exception as e:
            raise ValueError(f"Failed to read PDF: {e}")
            
    elif ext in [".docx", ".doc"]:
        try:
            doc = Document(str(file_path))
            for p in doc.paragraphs:
                if p.text.strip():
                    text += p.text + "\n"
            # Extract text from tables if any
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        text += row_text + "\n"
        except Exception as e:
            raise ValueError(f"Failed to read DOCX: {e}")
            
    elif ext == ".txt":
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            text = file_path.read_text(encoding="latin-1")

    elif ext == ".tex":
        try:
            raw_tex = file_path.read_text(encoding="utf-8")
        except Exception:
            raw_tex = file_path.read_text(encoding="latin-1")
        # Extract hrefs: \href{URL}{TEXT} -> TEXT (URL)
        hrefs = re.findall(r'\\href\{([^}]+)\}\{([^}]+)\}', raw_tex)
        for u, label in hrefs:
            if u not in discovered_links:
                discovered_links.append(u)
        # Strip comments & basic latex markup
        no_comments = re.sub(r'%.*$', '', raw_tex, flags=re.MULTILINE)
        clean = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?', ' ', no_comments)
        text = clean
    else:
        raise ValueError(f"Unsupported file format: {ext}")
        
    cleaned = _clean_unicode_characters(text.strip())
    if len(cleaned) < 30:
        raise ValueError("Extracted text is too short or unreadable.")
        
    # Append structured annotations section if URLs were recovered
    if discovered_links:
        link_block = "\n\n--- Extracted Document Hyperlinks & Contact Annotations ---\n"
        for l in discovered_links:
            if "linkedin.com" in l.lower():
                link_block += f"- LinkedIn Profile: {l}\n"
            elif "github.com" in l.lower():
                link_block += f"- GitHub Profile: {l}\n"
            elif "mailto:" in l.lower():
                link_block += f"- Email Link: {l.replace('mailto:', '')}\n"
            else:
                link_block += f"- Web / Portfolio / Project: {l}\n"
        cleaned += link_block

    return cleaned


# ── Heuristic Fallback Parser ────────────────────────────────────────────────

def _heuristic_parse_resume(raw_text: str, links: Optional[List[str]] = None) -> StructuredResumeProfile:
    """Deterministic, robust fallback parser when AI API is unreachable or times out."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    name = lines[0] if lines else "Candidate"
    # Filter common non-name headers
    if any(k in name.lower() for k in ["resume", "curriculum", "page 1", "email", "phone"]):
        name = lines[1] if len(lines) > 1 else "Candidate"

    # Email
    email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_text)
    email = email_match.group(0) if email_match else None

    # Phone
    phone_match = re.search(r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}', raw_text)
    phone = phone_match.group(0) if phone_match else None

    # Links
    all_links = list(links or [])
    found_urls = re.findall(r'https?://[^\s)\]">]+', raw_text)
    for u in found_urls:
        if u not in all_links:
            all_links.append(u)

    linkedin = next((l for l in all_links if "linkedin.com/in/" in l.lower()), None)
    github = next((l for l in all_links if "github.com/" in l.lower()), None)
    portfolio = next((l for l in all_links if not any(x in l.lower() for x in ["linkedin.com", "github.com", "huggingface.co"])), None)

    # Location
    location = None
    loc_match = re.search(r'([A-Z][a-zA-Z\s]+,\s*(?:UP|Delhi|Noida|Gurgaon|Bangalore|Karnataka|Maharashtra|Mumbai|Pune|California|CA|NY|USA|India))', raw_text)
    if loc_match:
        location = loc_match.group(1).strip()
    elif "india" in raw_text.lower():
        location = "India (Open to Remote / Hybrid)"

    # Skills detection with specific models & frameworks
    tech_catalog = [
        "Python", "SQL", "TypeScript", "JavaScript", "KQL",
        "AI Agents", "RAG", "LLM Fine-Tuning", "PEFT", "QLoRA", "NLP",
        "Qwen2.5", "Qwen", "LLaMA", "DeepSeek", "Mistral",
        "FastAPI", "PyTorch", "Hugging Face", "LangChain", "LangGraph", "Pydantic", "SQLAlchemy", "Next.js", "Scikit-learn",
        "PostgreSQL", "MongoDB", "SQLite", "AES-256-GCM", "Clerk", "JWT",
        "AWS", "Docker", "vLLM", "RunPod", "Linux", "Apache Airflow", "Prometheus", "Locust", "Git", "GitHub"
    ]
    matched_skills = []
    text_lower = raw_text.lower()
    for tech in tech_catalog:
        if re.search(r'(?<![a-zA-Z0-9])' + re.escape(tech.lower()) + r'(?![a-zA-Z0-9])', text_lower):
            matched_skills.append(tech)

    # Experience years
    years = "2+ years"
    exp_m = re.search(r'(\d+(?:\.\d+)?)\+?\s*years?', text_lower)
    if exp_m:
        years = f"{exp_m.group(1)}+ years"

    # Education
    edu = None
    edu_m = re.search(r'(B\.Tech|Bachelor|Master|M\.Tech|B\.S\.|M\.S\.)[^\n]+', raw_text, re.IGNORECASE)
    if edu_m:
        edu = edu_m.group(0).strip()

    return StructuredResumeProfile(
        name=name,
        email=email,
        phone=phone,
        location=location,
        linkedin_url=linkedin,
        github_or_portfolio=portfolio or github,
        headline_or_role="AI/ML Engineer",
        years_of_experience=years,
        professional_summary="AI/ML Engineer experienced in LLMs, RAG pipelines, fine-tuning, and scalable inference deployment.",
        key_skills=matched_skills or ["Python", "PyTorch", "FastAPI", "Docker", "LLMs", "RAG"],
        key_projects_or_achievements=[line for line in lines if line.startswith("*")][:4],
        education=edu,
        links=all_links
    )


# ── AI Structuring with Pydantic Validation ─────────────────────────────────

def structure_resume_with_ai(raw_text: str, api_key: Optional[str] = None) -> StructuredResumeProfile:
    """
    Sends raw resume text and extracted annotations to Groq AI and parses into a validated Pydantic model.
    Falls back gracefully to robust heuristic parsing if API is unreachable.
    """
    schema_json = json.dumps(StructuredResumeProfile.model_json_schema(), indent=2)

    system_prompt = f"""You are an expert ATS and HR intelligence parser.
Your task is to parse raw resume text and extracted document hyperlinks, strictly adhering to this JSON Schema:

{schema_json}

Extraction Guidelines:
1. Contact & Socials:
   - Extract full candidate LinkedIn URL, GitHub profile, and portfolio website from the text OR the 'Extracted Document Hyperlinks' section.
   - Extract accurate email, phone with country code, and candidate location (city, state, country).
2. Role & Headline:
   - Identify candidate's core professional identity (e.g. 'AI/ML Engineer' or 'Machine Learning Engineer').
3. Experience Duration:
   - Estimate total relevant experience duration from work history dates (e.g. '2+ years' or '1-2 years').
4. Comprehensive Key Skills:
   - Extract fine-grained technical skills including programming languages, specific AI models/architectures (e.g. Qwen2.5, LLaMA, DeepSeek), frameworks (FastAPI, PyTorch, LangChain), databases, and infrastructure tools (Docker, vLLM, RunPod, AWS).
   - Never omit specific open-source LLMs or fine-tuning techniques mentioned (such as Qwen2.5, QLoRA, PEFT).
5. Projects & Impact:
   - Summarize top projects capturing metrics, architectures, tools used, and quantitative impact.
6. Education:
   - Include degree, specialization, university, location, CGPA/marks, and graduation years.

Output ONLY valid JSON adhering strictly to the schema, with no markdown backticks or conversational preamble."""

    user_msg = f"Resume Content:\n\n{raw_text[:7000]}\n\nParse and structure into the required JSON schema."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.15,
        "max_completion_tokens": 2500,
        "response_format": {"type": "json_object"}
    }
    groq_key = api_key or os.getenv("GROQ_API_KEY", "") or GROQ_API_KEY

    if not groq_key:
        logger.warning("No GROQ_API_KEY available; using heuristic parser.")
        return _heuristic_parse_resume(raw_text)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {groq_key}",
    }

    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
        parsed_profile = StructuredResumeProfile.model_validate_json(content)
        return parsed_profile
    except Exception as e:
        logger.warning(f"AI resume parsing encountered error: {e}. Falling back to deterministic heuristic extractor.")
        return _heuristic_parse_resume(raw_text)


def format_candidate_context_for_prompt(profile: StructuredResumeProfile) -> str:
    """Formats the Pydantic structured resume into a prompt injection context for drafting emails."""
    skills_str = ", ".join(profile.key_skills) if profile.key_skills else "AI, Machine Learning, Python"
    projects_str = "\n".join([f"- {p}" for p in profile.key_projects_or_achievements]) if profile.key_projects_or_achievements else "N/A"

    portfolio = "https://parthml.in"
    for lk in (profile.links or []):
        if "parthml" in lk:
            portfolio = "https://parthml.in"
            break
        elif "portfolio" in lk or "github" in lk:
            portfolio = lk if lk.startswith("http") else f"https://{lk}"

    ctx = f"""Candidate Profile (Extracted from Resume):
- Name: {profile.name}
- Target Role / Headline: {profile.headline_or_role}
- Experience: {profile.years_of_experience or '2+ years'}
- Location: {profile.location or 'India (Open to Remote / Hybrid)'}
- Portfolio / Website: {portfolio}
- Key Skills: {skills_str}
- Professional Summary: {profile.professional_summary}
- Notable Projects / Accomplishments:
{projects_str}
- Contact Details: Email: {profile.email or ''} | Phone: {profile.phone or ''} | LinkedIn: {profile.linkedin_url or ''}"""
    return _clean_unicode_characters(ctx)
