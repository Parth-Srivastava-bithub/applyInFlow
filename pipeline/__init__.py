"""
pipeline/ — AutoApply AI & processing stage modules.

Each sub-module owns one stage of the pipeline with a clear input/output contract:
  - name_cleaner.py      : clean raw scraped names before any template/email use
  - relevance_scorer.py  : score how well a job post matches the candidate
  - email_drafter.py     : draft a cold email for a given HR contact
  - resume_tailor.py     : tailor the LaTeX master resume for a specific job post

All AI prompts are loaded from the top-level prompts/ directory so they can be
iterated on without touching any Python logic.
"""
