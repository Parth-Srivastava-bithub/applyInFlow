# LinkedIn Recruiter & HR Profile Scraper (Playwright)

A modular, resilient Playwright-based scraper in Python designed to search and extract publicly visible LinkedIn HR/Recruiter profile information using an existing authenticated browser session.

---

## Features

- **Existing Session Reuse**: Connects directly to an open Chrome session via Chrome DevTools Protocol (CDP) on port 9222 — no automated login or credentials needed.
- **Targeted Profile Extraction**: Extracts:
  - Full Name (`name`)
  - Headline / Job Title (`title`)
  - Current Company (`company`)
  - Location (`location`)
  - Normalized Profile URL (`linkedin_url`)
  - Publicly visible Gmail address (`gmail`), if present in headline, about summary, or visible cards.
- **Relevance Filtering**: Automatically filters out profiles that do not match HR, Recruiting, or Talent Acquisition roles.
- **Deduplication**: Tracks seen profile URLs across search pages and queries to prevent duplicate scraping.
- **Polite Pacing & Retries**: Employs configurable human-like random delays (jitter) and exponential backoff retry on transient page load failures.
- **Structured Export**: Saves output to formatted JSON (`output/recruiter_profiles.json`) and CSV (`output/recruiter_profiles.csv`).

---

## Project Structure

```
AutoApply/
├── config.py             # Settings, selectors, delays, and regexes
├── browser.py            # CDP browser connection & login verification
├── search.py             # LinkedIn search query execution & pagination
├── parser.py             # DOM parsing & public email extraction
├── filter.py             # HR/Recruiter keyword relevance filtering
├── storage.py            # Deduplication & JSON/CSV persistence
├── scraper.py            # Core scraping engine orchestrator
├── run.py                # CLI runner script
├── start_chrome.bat      # Helper script to launch Chrome with remote debugging
├── test_unit.py          # Unit tests
└── requirements.txt      # Python dependencies
```

---

## Docker Quick Start (Recommended)

Run AutoApply completely containerized with Chrome CDP and persistent cookie storage:

### 1. Launch with Docker Compose
```bash
docker compose up -d --build
```
*(Or double-click `docker_run.bat` on Windows)*

### 2. Complete 1-Time LinkedIn Login
1. Open **`http://localhost:6080`** in your browser to access the live container desktop via noVNC.
2. Log into your LinkedIn account (and complete any 2FA/SMS verification).
3. **That's it!** All cookies, tokens, and session data are automatically persisted in `./chrome_profile` on your host machine. You will never need to log in again.

### 3. Open AutoApply Dashboard
Open **`http://localhost:5000`** to search LinkedIn, generate Groq AI tailored cold emails, and send applications!

---

## Local Python Setup (Alternative)

### 1. Activate Virtual Environment
The `.venv` environment is already created and configured. Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```
*(Or run commands directly via `.\.venv\Scripts\python.exe run.py`)*

If you ever need to reinstall dependencies:
```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2. Launch Chrome with Remote Debugging

Close any existing Chrome instances or run the helper script:

**On Windows:**
Double-click `start_chrome.bat` or run:
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.chrome-playwright-session" https://www.linkedin.com
```

### 3. Log In to LinkedIn
In the Chrome window that opens, log into LinkedIn manually.

### 4. Run the Job Scraper (AI/ML Engineer Jobs)
To search for AI/ML Engineer jobs and extract them into a JSON file:

```powershell
# Search AI/ML Engineer jobs (default: 10 jobs)
python run_jobs.py -q "AI ML Engineer"

# Search with location filter (e.g. Remote):
python run_jobs.py -q "AI ML Engineer" "Machine Learning Engineer" -l "Remote" -m 20

# Fast mode (metadata only, skip visiting full descriptions):
python run_jobs.py -q "Generative AI Engineer" --no-description
```

The scraped jobs will be saved to [**`output/aiml_jobs.json`**](file:///c:/Users/user/Documents/AutoApply/output/aiml_jobs.json) and [**`output/aiml_jobs.csv`**](file:///c:/Users/user/Documents/AutoApply/output/aiml_jobs.csv).

---

### 5. Run the Recruiter Profile Scraper
```powershell
python run.py -q "Technical Recruiter" -p 1 -m 3
```

---

## Output Data Format

Results are incrementally saved to `output/recruiter_profiles.json`:

```json
[
  {
    "name": "Sarah Connor",
    "title": "Senior Technical Recruiter @ TechCorp",
    "company": "TechCorp",
    "location": "San Francisco Bay Area",
    "linkedin_url": "https://www.linkedin.com/in/sarah-connor-example",
    "gmail": "sarah.connor.recruiting@gmail.com"
  },
  {
    "name": "Alex Smith",
    "title": "Talent Acquisition Specialist",
    "company": "Global Innovations",
    "location": "New York, NY",
    "linkedin_url": "https://www.linkedin.com/in/alex-smith-example",
    "gmail": null
  }
]
```

---

## CLI Options

| Argument | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `--query` | `-q` | Default HR queries | Space-separated list of search queries |
| `--pages` | `-p` | `2` | Maximum search result pages per query |
| `--max-profiles` | `-m` | `15` | Maximum profiles to extract per query |
| `--cdp-url` | | `http://localhost:9222` | Chrome DevTools Protocol endpoint |
| `--min-delay` | | `2.5` | Minimum seconds between profile visits |
| `--max-delay` | | `5.0` | Maximum seconds between profile visits |
| `--output-json` | | `output/recruiter_profiles.json` | Path to export JSON |
| `--output-csv` | | `output/recruiter_profiles.csv` | Path to export CSV |
