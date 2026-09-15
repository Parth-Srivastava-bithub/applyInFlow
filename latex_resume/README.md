# Self-Hosted LaTeX-to-PDF Compiler API (FastAPI & Docker)

A minimal, secure, self-hosted microservice that compiles LaTeX documents (including `resume.tex` with XeLaTeX, `fontawesome5`, custom fonts, and formatting) to PDF via an HTTP API.

---

## Features

- **FastAPI Endpoint**: `POST /compile` accepts raw LaTeX string and returns the compiled binary PDF.
- **XeLaTeX Support**: Default compilation with `xelatex`, fully compatible with `fontawesome5`, `geometry`, `titlesec`, and modern fonts.
- **Secure Sandbox**:
  - Runs as a **non-root user** (`appuser:1000`).
  - Strict `-no-shell-escape` flag prevents `\write18` arbitrary command execution.
  - `-interaction=nonstopmode` and `-halt-on-error` prevent hanging processes.
  - Per-request compilation timeout (default: 30s).
  - Maximum payload size validation (2 MB).
- **Zero Disk Leakage**: Every compilation is executed in an isolated temporary directory and cleaned up immediately upon response or failure.
- **Helpful Error Output**: If compilation fails, returns HTTP `400` with the exact lines from the LaTeX error log.

---

## Project Structure

```text
latex_resume/
├── Dockerfile            # Debian-based image with Python, XeLaTeX, TeX Live & FontAwesome
├── docker-compose.yml    # Container orchestration with resource & security limits
├── main.py               # FastAPI backend with /compile and /health
├── requirements.txt      # FastAPI & Uvicorn dependencies
├── resume.tex            # Your master resume LaTeX source code
└── README.md             # Documentation and curl examples
```

---

## Quick Start with Docker

### 1. Build and Run via Docker Compose

```bash
docker-compose up -d --build
```

The service will be available at `http://localhost:8001`.

### 2. Check Health

```bash
curl http://localhost:8001/health
```

Expected output:
```json
{"status":"ok","service":"latex-compiler","engine":"xelatex"}
```

---

## API Documentation

### `POST /compile`

**Request Body (`application/json`)**:

```json
{
  "tex": "\documentclass{article}\begin{document}Hello World\end{document}",
  "engine": "xelatex",
  "runs": 1,
  "timeout_seconds": 30
}
```

**Parameters**:
- `tex` *(string, required)*: The LaTeX source code.
- `engine` *(string, optional)*: Compiler engine (`"xelatex"` or `"pdflatex"`, default: `"xelatex"`).
- `runs` *(integer, optional)*: Number of compiler passes (`1` or `2`, default: `1`).
- `timeout_seconds` *(integer, optional)*: Timeout in seconds (5-60, default: `30`).

**Response**:
- `200 OK`: Binary PDF file (`application/pdf`).
- `400 Bad Request`: JSON error details and log extract if LaTeX syntax is invalid.
- `413 Payload Too Large`: If `.tex` source exceeds 2 MB.
- `504 Gateway Timeout`: If compilation exceeds timeout limit.

---

## Example `curl` Commands

### Example A: Compiling `resume.tex` directly to `output.pdf`

Using `python` or `jq` to safely wrap `resume.tex` into a JSON payload:

**On Linux / macOS / Git Bash:**
```bash
python3 -c 'import json; print(json.dumps({"tex": open("resume.tex").read(), "engine": "xelatex"}))' | \
  curl -X POST http://localhost:8001/compile \
  -H "Content-Type: application/json" \
  -d @- \
  --output output.pdf
```

**On Windows PowerShell:**
```powershell
$texContent = Get-Content -Raw -Path "resume.tex"
$payload = @{ tex = $texContent; engine = "xelatex" } | ConvertTo-Json -Depth 2
Invoke-RestMethod -Uri "http://localhost:8001/compile" -Method Post -Body $payload -ContentType "application/json" -OutFile "output.pdf"
```

### Example B: Simple Inline LaTeX Test

```bash
curl -X POST http://localhost:8001/compile \
  -H "Content-Type: application/json" \
  -d '{"tex": "\\documentclass{article}\\begin{document}Hello from FastAPI and XeLaTeX!\\end{document}"}' \
  --output test.pdf
```

---

## Running Locally without Docker (Development)

If you have XeLaTeX and Python installed on your local machine:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
