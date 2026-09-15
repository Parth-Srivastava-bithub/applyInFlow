"""Configuration settings for LinkedIn Playwright Scraper."""

import re
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_OUTPUT_JSON = OUTPUT_DIR / "recruiter_profiles.json"
DEFAULT_OUTPUT_CSV = OUTPUT_DIR / "recruiter_profiles.csv"
SEEN_URLS_FILE = OUTPUT_DIR / "seen_urls.json"

# Browser Connection Settings
CDP_URL = "http://localhost:9222"
DEFAULT_USER_DATA_DIR = BASE_DIR / "browser_profile"

# Navigation & Timing Settings (in seconds)
PAGE_LOAD_TIMEOUT = 30000  # 30 seconds in ms
MIN_DELAY = 2.5
MAX_DELAY = 5.0
SEARCH_PAGE_DELAY = 3.0
MAX_RETRIES = 3

# Search Queries
DEFAULT_QUERIES = [
    "HR recruiter",
    "Talent Acquisition",
    "HR Manager",
    "Technical Recruiter",
    "Recruitment Specialist"
]

# HR / Recruiting Relevance Keywords (Lowercased for matching)
HR_KEYWORDS = [
    "recruiter",
    "recruitment",
    "talent acquisition",
    "talent partner",
    "talent specialist",
    "talent lead",
    "headhunter",
    "human resources",
    "hr manager",
    "hr generalist",
    "hr specialist",
    "hr director",
    "hr business partner",
    "hrbp",
    "people partner",
    "people operations",
    "people & culture",
    "technical recruiter",
    "talent sourcer",
    "sourcing specialist",
    "staffing specialist",
    "staffing consultant",
    "hiring manager",
]

# Regular Expression for Email Extraction (Extracts Gmail and general emails from public text)
GMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@gmail\.com\b", re.IGNORECASE)
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", re.IGNORECASE)

# LinkedIn DOM Selectors (Configured with multiple fallback selectors for resilience)
SELECTORS = {
    "name": [
        "h1.text-heading-xlarge",
        "section.artdeco-card h1",
        "div.ph5 h1",
        "h1.inline.t-24.t-black.t-normal.break-words",
        "h1",
    ],
    "headline": [
        "div.text-body-medium.break-words",
        "div.ph5 div.text-body-medium",
        "div.top-card-layout__headline",
        "h2.top-card-layout__headline",
        ".pv-text-details__left-panel div.text-body-medium",
    ],
    "location": [
        "span.text-body-small.inline.t-black--light.break-words",
        "div.ph5 span.text-body-small",
        "span.top-card__subline-item",
        "ul.pv-text-details__right-panel + ul li span",
        "div.pv-text-details__left-panel span.text-body-small",
    ],
    "current_company": [
        "button[aria-label^='Current company:'] span",
        "ul.pv-text-details__right-panel button[aria-label*='company']",
        "div[aria-label='Current company']",
        "li.experience-item span.t-14.t-bold",
        "section#experience-section li h3",
    ],
    "about_section": [
        "section#about ~ div .inline-show-more-text",
        "div#about ~ div span[aria-hidden='true']",
        "section:has(#about) .display-flex span[aria-hidden='true']",
        "section:has(h2:has-text('About')) div.inline-show-more-text",
    ],
    "search_results_container": [
        "ul.reusable-search__entity-result-list",
        "div.search-results-container",
        "ul.search-results__list",
    ],
    "search_result_items": [
        "li.reusable-search__result-container",
        "li.artdeco-list__item",
        "div.entity-result",
    ],
    "search_profile_links": [
        "a.app-aware-link[href*='/in/']",
        "span.entity-result__title-text a[href*='/in/']",
        "a[href*='linkedin.com/in/']",
    ],
    "pagination_next_button": [
        "button.artdeco-pagination__button--next",
        "button[aria-label='Next']",
        "button.artdeco-pagination__button--next:not([disabled])",
    ]
}
