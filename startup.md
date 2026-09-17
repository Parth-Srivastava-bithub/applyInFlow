# AutoApply — Complete Startup & Operations Guide

Yeh guide me AutoApply ko scratch se run karne aur close karne ka complete flow likha hai (Windows aur macOS / Linux dono ke liye).

---

## 🚀 How to Run AutoApply From Scratch (Step-by-Step)

AutoApply ecosystem me **3 components** hote hain:

```
[1. Docker LaTeX Compiler] ➔ Compiles resume.tex via XeLaTeX (Port 8001)
[2. Google Chrome with CDP] ➔ Scrapes LinkedIn HR posts safely without bot detection (Port 9222)
[3. Python Server & Web UI] ➔ Flask API, Groq AI tailoring & cold email pipeline (Port 5000)
```

---

### Step 1: Start LaTeX Compiler Microservice (Docker)
Resumes ko custom PDF me compile karne ke liye XeLaTeX microservice:

```bash
cd latex_resume
docker-compose up -d
cd ..
```
*Check if running:*
```bash
curl http://localhost:8001/health
# Response: {"status":"ok","service":"latex-compiler","engine":"xelatex"}
```

---

### Step 2: Install AutoApply Chrome Extension (Recommended — Zero CDP Needed!)

Ab aapko **koi batch file ya port 9222 chalane ki zaroorat nahi hai**. AutoApply Chrome Extension aapke normal browser session se directly scrape karta hai.

1. Chrome me open karein: `chrome://extensions`
2. Top-right me **Developer mode** toggle ko **ON** karein.
3. Top-left me **"Load unpacked"** par click karein aur project ka `extension` folder select karein:
   ```text
   AutoApply/extension
   ```
4. Bas! Extension load ho jayega aur Dashboard ke saath automatically connect ho jayega.

> 💡 **LinkedIn Safety Tip**: Hamesha Chrome me ek **alternate / secondary LinkedIn account** login rakhein scraping ke liye, taaki aapka main personal profile safe rahe.

---

### (Optional) Legacy Mode: Chrome with Remote Debugging (Port 9222)
*Agar aap extension use nahi karna chahte aur local Playwright CDP se scrape karna chahte hain:*
* **Windows**: `.\start_chrome.ps1` ya `.\start_chrome.bat`
* **macOS / Linux**: `./start_chrome.sh`

---

### Step 3: Start Python Backend & Web Dashboard

#### **Windows (PowerShell / CMD)**:
```powershell
# Virtual environment activate karein
.\.venv\Scripts\Activate.ps1

# Server start karein
python server.py
```

#### **macOS / Linux**:
```bash
# Virtual environment activate karein
source .venv/bin/activate

# Server start karein
python3 server.py
```

---

### Step 4: Open Dashboard & Use!

Browser me open karein:
👉 **`http://localhost:5000`**

1. **Sign In** par click karein.
2. **Settings (⚙)** open karke apna free **Groq API Key** aur Gmail app password daalein (agar pehle se `.env` me nahi hai).
3. **Step 1**: Target role (e.g. `AI ML Engineer`) daal kar **"Search LinkedIn for HR Emails"** par click karein.
4. **Step 2**: Apna master resume upload ya review karein.
5. **Step 3 & 4**: **Generate Drafts** par click karein, match scores dekhein, resume customize karein aur personalized emails send karein!

---

## 📋 Summary of Quick Commands

| Component | Windows (PowerShell) | macOS / Linux | Port |
| :--- | :--- | :--- | :--- |
| **1. LaTeX Docker** | `docker-compose -f latex_resume/docker-compose.yml up -d` | `docker-compose -f latex_resume/docker-compose.yml up -d` | `8001` |
| **2. Chrome Extension** | Load `AutoApply/extension` via `chrome://extensions` | Load `AutoApply/extension` via `chrome://extensions` | — |
| **3. Web App** | `python server.py` | `python3 server.py` | `5000` |
| **Dashboard URL**| Open `http://localhost:5000` | Open `http://localhost:5000` | — |
