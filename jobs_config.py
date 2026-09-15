"""Job-specific selectors, defaults, and configuration for LinkedIn Job Scraper."""

from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_JOBS_JSON = OUTPUT_DIR / "aiml_jobs.json"
DEFAULT_JOBS_CSV = OUTPUT_DIR / "aiml_jobs.csv"
SEEN_JOBS_FILE = OUTPUT_DIR / "seen_jobs.json"

# Default AI/ML Job Queries
DEFAULT_AIML_QUERIES = [
    "AI ML Engineer",
    "Machine Learning Engineer",
    "AI Engineer",
    "Generative AI Engineer",
    "LLM Engineer"
]

# LinkedIn Job Search DOM Selectors
JOB_SELECTORS = {
    "job_card": [
        "li.jobs-search-results__list-item",
        "div.job-card-container",
        "li.scaffold-layout__list-item",
        "div.base-card",
        "li[data-occludable-job-id]",
    ],
    "job_title": [
        "a.job-card-list__title",
        "a.job-card-container__link",
        "h3.base-search-card__title",
        ".artdeco-entity-lockup__title a",
        "h2.topcard__title",
        "h1.top-card-layout__title",
        "h1.t-24.t-bold",
    ],
    "company_name": [
        "div.artdeco-entity-lockup__subtitle",
        "span.job-card-container__primary-description",
        "h4.base-search-card__subtitle a",
        "a.topcard__org-name-link",
        "div.job-details-jobs-unified-top-card__company-name a",
        "span.jobs-unified-top-card__company-name",
    ],
    "location": [
        "ul.job-card-container__metadata-wrapper li",
        "div.artdeco-entity-lockup__caption span",
        "span.job-search-card__location",
        "span.topcard__flavor--bullet",
        "span.jobs-unified-top-card__bullet",
    ],
    "posted_time": [
        "time.job-search-card__listdate",
        "time",
        "li.job-card-container__footer-item",
        "span.jobs-unified-top-card__posted-date",
    ],
    "description": [
        "#job-details",
        "div.jobs-description-content__text",
        "div.jobs-description__content",
        "div.jobs-box__html-content",
        "div.show-more-less-html__markup",
        "article.jobs-description__container",
        "section.job-details-description",
        "div.description__text",
    ],
    "workplace_type": [
        "span.job-card-container__workplace-type",
        "span.jobs-unified-top-card__workplace-type",
    ]
}
