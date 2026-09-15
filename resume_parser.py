"""
Resume Parser Middleware with Pydantic BaseModel validation & Groq AI structuring.
Extracts raw text from PDF/DOCX/TXT resumes and structures them into a validated Pydantic model.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

import pypdf
import requests
from docx import Document
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


# ── Pydantic Schema for Structured Resume ────────────────────────────────────

class StructuredResumeProfile(BaseModel):
    name: str = Field(..., description="Candidate's full name")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    location: Optional[str] = Field(None, description="Location / City / Country")
    linkedin_url: Optional[str] = Field(None, description="LinkedIn profile URL")
    github_or_portfolio: Optional[str] = Field(None, description="GitHub or Portfolio link")
    
    headline_or_role: str = Field(..., description="Candidate's target role or professional headline (e.g. AI / ML Engineer)")
    years_of_experience: Optional[str] = Field(None, description="Estimated total years of relevant experience")
    professional_summary: str = Field(..., description="Compelling 2-3 sentence executive summary of background and technical strength")
    
    key_skills: List[str] = Field(default_factory=list, description="List of top 8-15 technical skills, tools, and frameworks")
    key_projects_or_achievements: List[str] = Field(
        default_factory=list,
        description="Top 3-5 high-impact projects, quantifiable achievements, or work accomplishments"
    )
    education: Optional[str] = Field(None, description="Highest degree and university / institution")


# ── Text Extractor ───────────────────────────────────────────────────────────

def extract_text_from_file(file_path: Path) -> str:
    """Extract raw text from PDF, DOCX, or TXT."""
    ext = file_path.suffix.lower()
    text = ""
    
    if ext == ".pdf":
        try:
            reader = pypdf.PdfReader(str(file_path))
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        except Exception as e:
            raise ValueError(f"Failed to read PDF: {e}")
            
    elif ext in [".docx", ".doc"]:
        try:
            doc = Document(str(file_path))
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        except Exception as e:
            raise ValueError(f"Failed to read DOCX: {e}")
            
    elif ext == ".txt":
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            text = file_path.read_text(encoding="latin-1")
    else:
        raise ValueError(f"Unsupported file format: {ext}")
        
    cleaned = text.strip()
    if len(cleaned) < 30:
        raise ValueError("Extracted text is too short or unreadable.")
    return cleaned


# ── AI Structuring with Pydantic Validation ─────────────────────────────────

def structure_resume_with_ai(raw_text: str) -> StructuredResumeProfile:
    """Sends raw resume text to Groq AI and parses it into a validated Pydantic model."""
    schema_json = json.dumps(StructuredResumeProfile.model_json_schema(), indent=2)

    system_prompt = f"""You are an expert ATS and HR intelligence parser.
Your task is to parse raw resume text and extract all relevant details strictly adhering to this JSON Schema:

{schema_json}

Rules:
- Extract accurate, truthful information from the resume text only.
- Highlight the strongest machine learning, AI, and software engineering capabilities.
- Summarize top projects with concrete metrics / impact where possible.
- Output ONLY valid JSON, no conversational text, no markdown backticks."""

    user_msg = f"Resume Content:\n\n{raw_text[:6000]}\n\nParse and structure into the required JSON schema."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_completion_tokens": 2048,
        "response_format": {"type": "json_object"}
    }
    groq_key = os.getenv("GROQ_API_KEY", "") or GROQ_API_KEY
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {groq_key}",
    }

    resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    
    # Strip any potential markdown wrappers
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()

    # Validate against Pydantic BaseModel
    parsed_profile = StructuredResumeProfile.model_validate_json(content)
    return parsed_profile


def format_candidate_context_for_prompt(profile: StructuredResumeProfile) -> str:
    """Formats the Pydantic structured resume into a prompt injection context for drafting emails."""
    skills_str = ", ".join(profile.key_skills) if profile.key_skills else "AI, Machine Learning, Python"
    projects_str = "\n".join([f"- {p}" for p in profile.key_projects_or_achievements]) if profile.key_projects_or_achievements else "N/A"

    return f"""Candidate Profile (Extracted from Resume):
- Name: {profile.name}
- Target Role / Headline: {profile.headline_or_role}
- Experience: {profile.years_of_experience or '2+ years'}
- Location: {profile.location or 'India (Open to Remote / Hybrid)'}
- Key Skills: {skills_str}
- Professional Summary: {profile.professional_summary}
- Notable Projects / Accomplishments:
{projects_str}
- Contact Details: Email: {profile.email or ''} | Phone: {profile.phone or ''} | LinkedIn: {profile.linkedin_url or ''}"""
