"""
pipeline/resume_tailor.py — Isolated resume-tailoring stage.

Single public function: tailor_resume()

Input/Output contract:
    Input:  base LaTeX source + job context (all strings)
    Output: tailored LaTeX source string

The system prompt is loaded from prompts/resume_tailor.txt so it can be
edited without touching this file.  Uses string.Template for $cand_name.

No Flask, no globals, no mutable state — independently testable.
"""

import json
import logging
import os
import re
from pathlib import Path
from string import Template
from typing import Any, Dict

import requests

logger = logging.getLogger("autoapply.resume_tailor")

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "resume_tailor.txt"
_GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"
_OPENAI_URL  = "https://api.openai.com/v1/chat/completions"


def _load_prompt(cand_name: str) -> str:
    """Load and render the resume tailoring system prompt."""
    if _PROMPT_FILE.exists():
        template_src = _PROMPT_FILE.read_text(encoding="utf-8")
        return Template(template_src).safe_substitute(cand_name=cand_name)
    logger.warning("prompts/resume_tailor.txt not found — using inline fallback.")
    return (
        f"You are an elite LaTeX Resume Customization Specialist for {cand_name}. "
        "Tailor the master resume LaTeX source for the given job post. "
        "Output ONLY the complete LaTeX source starting with \\documentclass."
    )


def _call_api(messages: list, model: str, max_tokens: int = 8192) -> str:
    """Call Groq or OpenAI API (auto-detected) with streaming, return full text."""
    is_openai = any(model.startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt"))

    if is_openai:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            is_openai = False
            model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    if is_openai:
        url = _OPENAI_URL
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": True,
        }
    else:
        url = _GROQ_URL
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_completion_tokens": max_tokens,
            "top_p": 0.95,
            "stream": True,
            "stop": None,
        }
        if "oss" in model.lower():
            payload["reasoning_effort"] = "medium"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=90)
    resp.raise_for_status()

    full = ""
    for line in resp.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8")
        if s.startswith("data: "):
            d = s[6:].strip()
            if d == "[DONE]":
                break
            try:
                chunk = json.loads(d)
                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                full += content
            except Exception:
                pass

    return full.strip()


def _clean_latex_output(raw: str) -> str:
    """Strip markdown code fences and reasoning blocks from LLM output."""
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:latex|tex)?\n", "", clean)
        clean = re.sub(r"\n```$", "", clean)
    return clean.strip()


def tailor_resume(
    base_tex: str,
    post_text: str,
    hr_title: str,
    to_email: str,
    feedback: str,
    cand_name: str,
    model: str | None = None,
) -> str:
    """
    Tailor a LaTeX master resume for a specific job posting.

    Args:
        base_tex:   Full LaTeX source of the master resume.
        post_text:  Raw LinkedIn post or job description text.
        hr_title:   Recruiter's headline/role title.
        to_email:   Recipient HR email (used for context only).
        feedback:   Optional user revision instructions (e.g. "remove kafka").
        cand_name:  Candidate display name (e.g. "Parth Srivastava").
        model:      LLM model identifier. Defaults to GROQ_MODEL env var.

    Returns:
        Tailored LaTeX source string (starts with \\documentclass).

    Raises:
        requests.HTTPError: if the API call fails.
        ValueError: if the model returns empty or malformed output.
    """
    use_model = model or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    system_prompt = _load_prompt(cand_name)

    user_prompt = (
        f"Recipient Recruiter Email: {to_email}\n"
        f"Recruiter Role / Title: {hr_title}\n\n"
        f'Job Post Context:\n"""{post_text[:3000]}"""\n\n'
        f'User Revision Feedback (if any):\n'
        f'"""{feedback or "Tailor the skills and project bullet points to maximize alignment with this job post."}"""\n\n'
        f'Current Master LaTeX Resume:\n"""{base_tex}"""\n\n'
        "Provide the tailored complete LaTeX document now:"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    raw = _call_api(messages, use_model)
    result = _clean_latex_output(raw)

    if not result or "\\documentclass" not in result:
        raise ValueError(
            "LLM returned empty or invalid LaTeX. "
            f"Raw output (first 200 chars): {raw[:200]}"
        )

    logger.debug(f"Tailored resume for {to_email}: {len(result)} chars")
    return result
