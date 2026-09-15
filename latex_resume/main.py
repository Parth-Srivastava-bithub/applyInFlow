import os
import re
import shutil
import tempfile
import subprocess
from typing import Optional
from fastapi import FastAPI, HTTPException, Response, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="LaTeX-to-PDF Compiler API",
    description="Minimal, secure, self-hosted LaTeX to PDF compilation service using XeLaTeX.",
    version="1.0.0"
)

# Enable CORS for frontend integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Limits & Defaults
MAX_TEX_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB limit
DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 60

class CompileRequest(BaseModel):
    tex: str = Field(..., description="Raw LaTeX source code")
    engine: str = Field("xelatex", description="LaTeX compiler engine (xelatex or pdflatex)")
    runs: int = Field(1, ge=1, le=2, description="Number of compiler passes (1 or 2)")
    timeout_seconds: int = Field(DEFAULT_TIMEOUT_SEC, ge=5, le=MAX_TIMEOUT_SEC, description="Timeout in seconds")

def extract_latex_errors(log_content: str) -> str:
    """Extract relevant error lines from a LaTeX compilation log."""
    lines = log_content.splitlines()
    error_lines = []
    capture = False
    for line in lines:
        if line.startswith("!"):
            capture = True
            error_lines.append(line)
        elif capture:
            if line.strip() == "" or line.startswith("l."):
                error_lines.append(line)
                if line.startswith("l."):
                    capture = False
            elif len(error_lines) < 20:
                error_lines.append(line)
            else:
                capture = False
    if error_lines:
        return "\n".join(error_lines)
    # Fallback to last 25 lines if no '!' markers found
    return "\n".join(lines[-25:])

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "latex-compiler", "engine": "xelatex"}

@app.post("/compile", response_class=Response)
async def compile_latex(payload: CompileRequest):
    tex_code = payload.tex
    if not tex_code or not tex_code.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LaTeX source code cannot be empty."
        )

    # Size validation
    if len(tex_code.encode("utf-8")) > MAX_TEX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"LaTeX source exceeds maximum allowed size of {MAX_TEX_SIZE_BYTES // (1024 * 1024)}MB."
        )

    engine = payload.engine.strip().lower()
    if engine not in ["xelatex", "pdflatex", "lualatex"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported engine. Allowed engines: xelatex, pdflatex, lualatex."
        )

    timeout = min(max(5, payload.timeout_seconds), MAX_TIMEOUT_SEC)
    runs = min(max(1, payload.runs), 2)

    # Temporary directory ensures complete isolation and cleanup after every request
    with tempfile.TemporaryDirectory(prefix="latex_build_") as tmpdir:
        tex_path = os.path.join(tmpdir, "document.tex")
        pdf_path = os.path.join(tmpdir, "document.pdf")
        log_path = os.path.join(tmpdir, "document.log")

        # Write .tex file
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_code)

        # Build compiler command with security flags:
        # -no-shell-escape strictly prevents \write18 arbitrary command execution
        # -interaction=nonstopmode prevents hanging on error prompts
        # -halt-on-error stops on the first severe error
        cmd = [
            engine,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "document.tex"
        ]

        proc = None
        try:
            for _ in range(runs):
                proc = subprocess.run(
                    cmd,
                    cwd=tmpdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    text=True
                )
                if proc.returncode != 0:
                    break

        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"LaTeX compilation timed out after {timeout} seconds."
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to execute LaTeX compiler: {str(e)}"
            )

        if not os.path.exists(pdf_path):
            log_summary = ""
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                        log_summary = extract_latex_errors(lf.read())
                except Exception:
                    pass
            
            error_output = log_summary or (proc.stderr if proc else "") or (proc.stdout if proc else "")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "LaTeX compilation failed.",
                    "compiler_output": error_output.strip()
                }
            )

        # Read compiled PDF bytes
        with open(pdf_path, "rb") as pf:
            pdf_bytes = pf.read()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="compiled.pdf"',
            "Content-Length": str(len(pdf_bytes))
        }
    )
