# AutoApply — Complete Startup & Operations Guide

This guide provides everything needed to start and run the AutoApply ecosystem effectively.

---

## 🚀 Quick Start (All-in-One)

To run the complete system, launch the 3 components:

### 1. Start LaTeX Compiler Microservice (Docker)
Compiles customized resumes (`resume.tex`) into PDFs via XeLaTeX with `fontawesome5` support.

```bash
cd latex_resume
docker-compose up -d
```
*Verify it is running:*
```bash
curl http://localhost:8001/health
# Output: {"status":"ok","service":"latex-compiler","engine":"xelatex"}
```

---

### 2. Start Chrome with Remote Debugging (Port 9222)
Connects Playwright to your real Chrome session for scraping LinkedIn HR posts without login friction.

**On Windows (PowerShell or Command Prompt):**
```cmd
start_chrome.bat
```
*Or via PowerShell:*
```powershell
.\start_chrome.ps1
```

> **Note:** Log in to LinkedIn once in this browser window. Your login session & cookies will be preserved in `chrome_profile/`.

---

### 3. Start Python Backend & Web Dashboard
Runs the Flask API, Groq AI query generation, email generator, and resume tailoring engine.

```bash
# Windows
.venv\Scripts\python.exe server.py

# Linux / macOS
python3 server.py
```

Dashboard will be live at:
👉 **`http://localhost:5000`**

---

## 🛠️ Docker Management Commands (LaTeX Compiler)

| Action | Command |
| :--- | :--- |
| **Start in background** | `docker-compose up -d` (inside `latex_resume/`) |
| **View live logs** | `docker logs -f latex-compiler` |
| **Restart container** | `docker restart latex-compiler` |
| **Stop container** | `docker-compose down` |
| **Rebuild after code changes** | `docker-compose up -d --build` |

---

## 🎯 Features Workflow

1. **Step 1: Search Keywords**: Enter target roles (e.g. `AI ML Engineer`) and click **Search LinkedIn for HR Gmails**.
2. **Step 2: Resume Attachment**: Your master `latex_resume/resume.tex` is linked automatically.
3. **Step 3: Outreach Controls**: Choose AI model (Groq Qwen 3.8 27B / GPT-OSS 120B), filter criteria, and click **⚡ Generate Drafts**.
4. **Step 4: Match Fit & Interactive Tailoring**:
   - Every draft shows a **Match Score Badge** (`🟢 100% Match`, `[✓ Domain Fit]`).
   - Click **🎯 Tailor Resume** on any draft to open the **Live PDF Preview & AI Refinement Modal**.
   - Review page fit / layout, type feedback (e.g., *"add Docker, shorten bullet 2"*), or edit raw LaTeX directly.
   - Click **✅ Use Tailored Resume for this Email** to bind the custom PDF to that application.
5. **Send Email**: Sends via Gmail SMTP with the tailored PDF attached.

---

## 🧪 Testing Commands

### Test LaTeX Compilation:
```powershell
$tex = Get-Content -Raw -Path "latex_resume/resume.tex"
$payload = @{ tex = $tex; engine = "xelatex" } | ConvertTo-Json -Depth 2
Invoke-RestMethod -Uri "http://localhost:8001/compile" -Method Post -Body $payload -ContentType "application/json" -OutFile "output/test_resume.pdf"
```

### Test Backend Match & Tailoring API:
```powershell
Invoke-RestMethod -Uri "http://localhost:5000/api/resume/base-tex" -Method Get
```
