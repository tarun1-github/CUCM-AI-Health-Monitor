# CUCM AI Health Monitor

An AI-powered Cisco Unified Communications Manager (CUCM) remote health monitoring and diagnostic reporting platform.

## Features

- **Dual CUCM Connectivity**:
  - **SSH CLI (`NetmikoCUCMClient` & `ParamikoCUCMClient`)**: Custom Netmiko `CUCMConnection(CiscoIosSSH)` handler tailored for CUCM's `admin:` prompt, `set cli pagination off`, and `quit` cleanup, with a pure Paramiko interactive shell fallback.
  - **HTTPS AXL SOAP (`CUCMAXLClient`)**: Independent SOAP client on port `8443` with strict credential separation, read-only SQL validation, and configurable SSL verification.
- **Strict Read-Only Command Allowlist**: Defined in `config/commands.yaml` and enforced by `backend/utils/validators.py`.
- **Deterministic Health Classification**: Evaluates CPU, memory, disk, services, and network diagnostics into `HEALTHY`, `WARNING`, `CRITICAL`, or `UNKNOWN`.
- **Interchangeable AI / LLM Summarization**: Supports Ollama, Gemini, and OpenAI, with a deterministic Python natural-language fallback when no LLM is configured.
- **Premium Glassmorphism Dashboard**: FastAPI-served HTML/CSS/JS dashboard with 3D buttons, KPI cards, Chart.js gauges and trend charts, AI summary panel, and expandable raw CLI output viewer.
- **Multi-Format Reports**: Exports interactive HTML, styled PDF (ReportLab), CSV, and raw JSON.

## Setup & Startup (Windows PowerShell)

```powershell
cd E:\VoiceOpsAI\CUCM-AI-HealthMonitor
.\.venv\Scripts\Activate.ps1
.\scripts\run.ps1
```

Open `http://127.0.0.1:8000` in your browser.

## Running Unit Tests

```powershell
.\scripts\test.ps1
```

## Security Notes

- Never commit `.env` (already in `.gitignore`).
- AXL (`CUCM_USERNAME`/`CUCM_PASSWORD`) and CLI (`CUCM_CLI_USERNAME`/`CUCM_CLI_PASSWORD`) credentials are kept strictly separate.
- In `APP_ENV=production`, SSL verification and strict SSH host-key verification are enforced automatically.
