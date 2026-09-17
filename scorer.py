"""
AutoApply Match & Fit Scoring Engine
Evaluates how closely a candidate's background matches a LinkedIn job post
using Dynamic Proportional Normalization (scoring ONLY on requirements actually stated).
"""

import json
import logging
import os
import re
from pathlib import Path
from string import Template
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger("autoapply.scorer")

# Load from config so there is a single source of truth
try:
    from config import GROQ_URL, DEFAULT_MODEL as MODEL
except ImportError:
    GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

# Prompt file for LLM requirement extraction
_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "relevance_extract.txt"


def _load_extraction_prompt(post_text: str) -> str:
    """Load and render the requirement-extraction prompt from prompts/relevance_extract.txt."""
    if _PROMPT_FILE.exists():
        template_src = _PROMPT_FILE.read_text(encoding="utf-8")
        return Template(template_src).safe_substitute(post_text=post_text[:2500])
    # Inline fallback if prompt file is missing
    logger.warning("prompts/relevance_extract.txt not found — using inline fallback.")
    return (
        "Extract structured job requirements from this LinkedIn post. "
        "Output ONLY valid JSON with keys: min_experience_years, max_experience_years, "
        "required_skills, role_domain, work_mode, urgency.\n\n"
        f'Post:\n"""{post_text[:2500]}"""'
    )


def parse_candidate_years(exp_str: str) -> float:
    """Extract numeric years from strings like '2+ years building ML' or '8 months'."""
    if not exp_str:
        return 1.5
    exp_lower = exp_str.lower()
    
    # Check for months e.g. "8 months" -> 0.67
    m_months = re.search(r'(\d+)\s*month', exp_lower)
    if m_months:
        return round(float(m_months.group(1)) / 12.0, 1)

    # Check for years e.g. "2+ years", "3.5 years"
    m_years = re.search(r'(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*year', exp_lower)
    if m_years:
        return float(m_years.group(1))

    # Single digit fallback
    m_digit = re.search(r'\b(\d+)\b', exp_lower)
    if m_digit:
        return float(m_digit.group(1))
    return 1.5


def extract_post_requirements(post_text: str, hr_title: str = "") -> Dict[str, Any]:
    """
    Uses Groq LLM (JSON mode) to extract ONLY explicitly stated requirements from the post.
    If a field is not stated, it MUST return null / empty list.
    """
    if not post_text:
        return {
            "min_experience_years": None,
            "max_experience_years": None,
            "required_skills": [],
            "role_domain": hr_title or None,
            "work_mode": None,
            "urgency": None
        }

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return _regex_fallback_extract(post_text, hr_title)

    prompt = _load_extraction_prompt(post_text)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise JSON extractor. Output valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_completion_tokens": 512,
        "response_format": {"type": "json_object"}
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        data = json.loads(raw)
        return {
            "min_experience_years": data.get("min_experience_years"),
            "max_experience_years": data.get("max_experience_years"),
            "required_skills": [s.strip() for s in data.get("required_skills", []) if s and isinstance(s, str)],
            "role_domain": data.get("role_domain"),
            "work_mode": data.get("work_mode"),
            "urgency": data.get("urgency")
        }
    except Exception as e:
        logger.warning(f"LLM extraction fallback triggered: {e}")
        return _regex_fallback_extract(post_text, hr_title)


def _regex_fallback_extract(text: str, hr_title: str = "") -> Dict[str, Any]:
    """Fast regex heuristics when API is unreachable."""
    lower = text.lower()
    
    # Experience regex: "8+ years", "5-8 yrs", "min 3 years"
    min_exp = None
    exp_match = re.search(r'(\d+)\s*(?:-\s*\d+)?\s*(?:\+|plus)?\s*(?:yrs|years|yr)\b', lower)
    if exp_match:
        try:
            min_exp = float(exp_match.group(1))
        except Exception:
            pass

    # Common skills detection
    tech_keywords = [
        "python", "pytorch", "tensorflow", "fastapi", "docker", "langchain",
        "rag", "vllm", "qlora", "aws", "kubernetes", "react", "node", "sql",
        "java", "c++", "go", "golang", "nlp", "llm", "transformers", "kafka"
    ]
    found_skills = [kw.capitalize() for kw in tech_keywords if re.search(r'\b' + re.escape(kw) + r'\b', lower)]

    # Work mode
    work_mode = None
    if "remote" in lower or "wfh" in lower:
        work_mode = "remote"
    elif "hybrid" in lower:
        work_mode = "hybrid"
    elif "onsite" in lower or "on-site" in lower:
        work_mode = "onsite"

    # Urgency
    urgency = None
    if "immediate" in lower or "urgent" in lower or "15 days" in lower or "immediate joiner" in lower:
        urgency = "immediate"

    return {
        "min_experience_years": min_exp,
        "max_experience_years": None,
        "required_skills": found_skills[:6],
        "role_domain": hr_title or None,
        "work_mode": work_mode,
        "urgency": urgency
    }


def load_all_candidate_skills(candidate_profile: Dict[str, Any]) -> tuple[set, str]:
    """
    Extracts all candidate skills and full text from:
    1. latex_resume/resume.tex (the master source of truth!)
    2. output/structured_resume.json
    3. candidate_profile dictionary
    """
    skills_set = set()
    full_text = ""

    # 1. Master resume.tex
    tex_path = os.path.join(os.getcwd(), "latex_resume", "resume.tex")
    if not os.path.exists(tex_path):
        tex_path = os.path.join(os.path.dirname(__file__), "latex_resume", "resume.tex")

    if os.path.exists(tex_path):
        try:
            with open(tex_path, "r", encoding="utf-8", errors="ignore") as f:
                tex_content = f.read()
            full_text += " " + tex_content.lower()

            # Extract from \section*{Skills}
            skills_match = re.search(r'\\section\*\{Skills\}(.*?)(?:\\section|\Z)', tex_content, re.DOTALL)
            if skills_match:
                clean_skills = re.sub(r'\\[a-zA-Z]+\*?(?:\{.*?\})?', ' ', skills_match.group(1))
                clean_skills = re.sub(r'[{}\\_&%]', ' ', clean_skills)
                for token in re.split(r'[,|/•\n:\-]', clean_skills):
                    t = token.strip().lower()
                    if t and len(t) >= 2:
                        skills_set.add(t)
        except Exception:
            pass

    # 2. Structured resume
    struct_path = os.path.join(os.getcwd(), "output", "structured_resume.json")
    if os.path.exists(struct_path):
        try:
            with open(struct_path, "r", encoding="utf-8", errors="ignore") as f:
                struct_data = json.load(f)
            for s in struct_data.get("skills", []):
                if isinstance(s, str) and s.strip():
                    skills_set.add(s.strip().lower())
            for proj in struct_data.get("projects", []):
                for tech in proj.get("technologies", []):
                    if isinstance(tech, str) and tech.strip():
                        skills_set.add(tech.strip().lower())
        except Exception:
            pass

    # 3. Candidate profile
    if candidate_profile:
        cand_skills_raw = candidate_profile.get("skills", "")
        for s in re.split(r'[,|/•\n]', str(cand_skills_raw)):
            t = s.strip().lower()
            if t:
                skills_set.add(t)

    return skills_set, full_text


def is_skill_matched(req_skill: str, candidate_skills: set, full_resume_text: str, candidate_role: str) -> bool:
    """Checks if a required skill is covered by candidate skills or resume text."""
    req_clean = req_skill.strip().lower()
    if not req_clean:
        return False

    # Direct match in skills set or substring
    if req_clean in candidate_skills:
        return True
    if any(req_clean in cs or cs in req_clean for cs in candidate_skills if len(cs) >= 3):
        return True

    # Exact token or phrase match in full resume text (e.g. LangGraph, API, Vector Databases)
    clean_kw = re.sub(r'[^a-z0-9+#]', ' ', req_clean).strip()
    if clean_kw and re.search(r'\b' + re.escape(clean_kw) + r'\b', full_resume_text):
        return True

    # Multi-word alias checks
    if "langgraph" in req_clean and ("langgraph" in full_resume_text or "langgraph" in candidate_skills):
        return True
    if "api" in req_clean and ("api" in candidate_skills or "fastapi" in candidate_skills or "api" in full_resume_text):
        return True
    if "vector" in req_clean and ("vector" in full_resume_text or "rag" in candidate_skills or "rag" in full_resume_text):
        return True
    if "rag" in req_clean and ("rag" in candidate_skills or "rag" in full_resume_text):
        return True
    if ("llm" in req_clean or "large language" in req_clean) and ("llm" in candidate_skills or "fine-tuning" in full_resume_text):
        return True
    if "agent" in req_clean and ("agent" in candidate_skills or "agent" in full_resume_text):
        return True

    # Role overlap
    if req_clean in candidate_role:
        return True

    return False


def score_match(post_text: str, candidate_profile: Dict[str, Any], hr_title: str = "") -> Dict[str, Any]:
    """
    Computes candidate match score with Dynamic Proportional Normalization.
    Only evaluates requirements that are explicitly present in the post.
    """
    reqs = extract_post_requirements(post_text, hr_title)

    cand_skills_set, full_resume_text = load_all_candidate_skills(candidate_profile)
    cand_role = str(candidate_profile.get("role", "")).lower()
    cand_exp_val = parse_candidate_years(candidate_profile.get("experience", "2+ years"))
    cand_loc = str(candidate_profile.get("location", "")).lower()

    earned_points = 0.0
    max_possible_points = 0.0
    matched_tags: List[str] = []
    missing_tags: List[str] = []
    dealbreaker: Optional[str] = None
    evaluated_criteria_count = 0

    # ── 1. Experience Evaluation (Weight: 35 pts if stated) ───────────────────
    min_exp = reqs.get("min_experience_years")
    if min_exp is not None and isinstance(min_exp, (int, float)) and min_exp > 0:
        max_possible_points += 35.0
        evaluated_criteria_count += 1

        exp_diff = cand_exp_val - min_exp
        if exp_diff >= 0:
            # Candidate meets or exceeds
            earned_points += 35.0
            matched_tags.append(f"✓ {int(min_exp) if min_exp.is_integer() else min_exp}+ Yrs Exp")
        elif exp_diff >= -1.0:
            # Close gap (e.g. 2 yrs vs 3 yrs required) -> 20 pts
            earned_points += 20.0
            matched_tags.append(f"~ {int(min_exp)}+ Yrs Exp ({cand_exp_val:.1f}y)")
        elif exp_diff >= -2.5:
            # Moderate gap (e.g. 2 yrs vs 4 yrs required) -> 10 pts
            earned_points += 10.0
            missing_tags.append(f"✕ {int(min_exp)}+ Yrs Exp ({cand_exp_val:.1f}y)")
        else:
            # Severe gap! (e.g. 8 yrs required, candidate has 2 yrs or 8 mos)
            # 0 pts and triggers explicit Dealbreaker
            earned_points += 0.0
            dealbreaker = f"Experience gap: Post requires {int(min_exp)}+ years (You: ~{cand_exp_val:.1f} yrs)"
            missing_tags.append(f"✕ {int(min_exp)}+ Yrs Exp Required")

    # ── 2. Skills Evaluation (Weight: 35 pts if stated) ───────────────────────
    req_skills = reqs.get("required_skills", [])
    if req_skills:
        max_possible_points += 35.0
        evaluated_criteria_count += 1
        
        skill_matches = 0
        for s in req_skills:
            if is_skill_matched(s, cand_skills_set, full_resume_text, cand_role):
                skill_matches += 1
                matched_tags.append(f"✓ {s.strip()}")
            else:
                missing_tags.append(f"✕ {s.strip()}")

        match_ratio = skill_matches / len(req_skills)
        earned_points += (match_ratio * 35.0)

    # ── 3. Role / Domain Relevance (Weight: 20 pts if stated) ─────────────────
    role_domain = reqs.get("role_domain")
    if role_domain and len(str(role_domain).strip()) > 3:
        max_possible_points += 20.0
        evaluated_criteria_count += 1
        rd_lower = str(role_domain).lower()

        # Check keyword overlaps with candidate role
        domain_tokens = [t for t in re.split(r'[\s/,-]', rd_lower) if len(t) >= 3]
        role_matches = sum(1 for t in domain_tokens if t in cand_role or any(t in cs for cs in cand_skills_set))
        if role_matches >= 2 or ("ai" in rd_lower and "ai" in cand_role) or ("ml" in rd_lower and "ml" in cand_role):
            earned_points += 20.0
            matched_tags.append("✓ Domain Fit")
        elif role_matches == 1:
            earned_points += 12.0
            matched_tags.append("~ Related Domain")
        else:
            earned_points += 4.0
            missing_tags.append("✕ Domain Pivot")

    # ── 4. Work Mode / Logistics (Weight: 10 pts if stated) ───────────────────
    work_mode = reqs.get("work_mode")
    if work_mode:
        max_possible_points += 10.0
        evaluated_criteria_count += 1
        if work_mode == "remote":
            earned_points += 10.0
            matched_tags.append("✓ Remote")
        elif "open to remote" in cand_loc or "hybrid" in cand_loc:
            earned_points += 8.0
            matched_tags.append(f"✓ {work_mode.capitalize()}")
        else:
            earned_points += 5.0
            missing_tags.append(f"~ {work_mode.capitalize()}")

    # Urgency bonus (if mentioned in post)
    urgency = reqs.get("urgency")
    if urgency:
        matched_tags.append("⚡ Immediate / Urgent")

    # ── Proportional Normalization Calculation ───────────────────────────────
    if max_possible_points <= 0:
        # If the post was completely bare / general greeting without specific keywords
        final_score = 75
        evaluated_criteria_count = 0
        matched_tags.append("✓ General Opening")
    else:
        raw_pct = (earned_points / max_possible_points) * 100.0
        final_score = int(round(raw_pct))

    # Cap score if a severe dealbreaker was triggered
    if dealbreaker and final_score > 48:
        final_score = 45

    # Clamp 0 to 100
    final_score = max(5, min(100, final_score))

    # Determine fit tier
    if final_score >= 75:
        fit_tier = "strong"
    elif final_score >= 50:
        fit_tier = "moderate"
    else:
        fit_tier = "poor"

    return {
        "fit_score": final_score,
        "fit_tier": fit_tier,
        "dealbreaker": dealbreaker,
        "matched_tags": matched_tags[:8],
        "missing_tags": missing_tags[:6],
        "evaluated_fields_count": evaluated_criteria_count,
        "extracted_requirements": reqs
    }
