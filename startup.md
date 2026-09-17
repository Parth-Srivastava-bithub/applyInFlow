# AutoApply — Complete Startup & Operations Guide

Yeh guide me AutoApply ko scratch se run karne aur close karne ka complete flow likha hai (Windows aur macOS / Linux dono ke liye).

---

## 🛑 How to Stop / Close Everything (Scratch se run karne se pehle)

Agar purane processes chal rahe hain toh unhe band karne ke commands:

### **Windows (PowerShell)**:
```powershell
# 1. Stop Python Server (Port 5000)
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Stop Chrome Debugging (Port 9222) if needed
Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force

# 3. Stop Docker LaTeX compiler (optional)
docker stop latex-compiler
```

### **macOS / Linux**:
```bash
# 1. Stop Python Server (Port 5000)
pkill -f "python.*server.py"

# 2. Stop Chrome Debugging (Port 9222)
kill $(lsof -t -i:9222) 2>/dev/null

# 3. Stop Docker LaTeX compiler (optional)
docker stop latex-compiler
```

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

### Step 2: Start Google Chrome with Remote Debugging (Port 9222)

> ⚠️ **PowerShell Note**: PowerShell me `./` ya `.\` lagana zaroori hota hai current directory ke script ke aage!

#### **Windows (PowerShell)**:
```powershell
.\start_chrome.ps1
```
*(Ya CMD me: `start_chrome.bat`)*

#### **macOS / Linux**:
```bash
chmod +x start_chrome.sh
./start_chrome.sh
```

#### **Manual Command (Agar script use nahi karni)**:
* **Windows**:
  ```cmd
  "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="./chrome_profile" https://www.linkedin.com
  ```
* **macOS**:
  ```bash
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="./chrome_profile" https://www.linkedin.com
  ```
* **Linux**:
  ```bash
  google-chrome --remote-debugging-port=9222 --user-data-dir="./chrome_profile" https://www.linkedin.com
  ```

> 🔑 **1-Time Login**: Jo Chrome window khulegi, usme ek baar apna **LinkedIn account login** kar lein. Cookies `./chrome_profile` me save ho jayengi. Baar-baar login nahi karna padega!

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
| **2. Chrome CDP** | `.\start_chrome.bat` ya `.\start_chrome.ps1` | `./start_chrome.sh` | `9222` |
| **3. Web App** | `python server.py` | `python3 server.py` | `5000` |
| **Dashboard URL**| Open `http://localhost:5000` | Open `http://localhost:5000` | — |
