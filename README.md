# TraceIQ — PDM Document Traceability Agent

> A **local-first, AI-powered traceability agent** that detects conflicts and coverage gaps across your full PDM document chain — without a single byte leaving your machine.

![Python](https://img.shields.io/badge/Python-3.12-3776ab?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.138-009688?style=flat&logo=fastapi&logoColor=white)
![Qwen 2.5 7B](https://img.shields.io/badge/Qwen_2.5_7B-Q4__K__M-6366f1?style=flat)
![Ollama](https://img.shields.io/badge/Ollama-Local_Inference-black?style=flat)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

---

## The Problem

In enterprise product development, a single requirement change in a BRD cascades through CDD, PRD, SDD, Epics, and User Stories. **Nobody knows which downstream documents are now stale or contradictory** — until it causes a bug in production or a failed audit.

Existing tools check syntax. TraceIQ checks **meaning**.

---

## What It Does

Upload your PDM documents. TraceIQ runs a 5-step pipeline that:

1. **Parses** all markdown documents and extracts structured requirements
2. **Builds** a directed traceability graph (NetworkX) across the full document chain
3. **Validates** structural integrity — broken links, missing coverage, version mismatches, requirements with no acceptance criteria
4. **Analyzes** semantically linked requirement pairs using a local Qwen 2.5 7B model — detecting contradictions, scope drift, and SLA mismatches
5. **Generates** a classified conflict report streamed live to the browser via Server-Sent Events

All inference runs on local GPU via Ollama. **No API keys. No cloud. No data exposure.**

---

## Document Chain Supported

```
CONFLUENCE                      JIRA
──────────                      ────
LVC (Lean Value Case)    ──►  Portfolio Epic
TIA (Technical Impact)
BRD (Business Req)       ──►  Capabilities
CDD (Concept Design)
PRD (Product Req)        ──►  Features → Epics → User Stories
SDD (Solution Design)                              │
ADD (Architecture Assess)                          ▼
                                          Acceptance Criteria
```

---

## Conflict Types Detected

| Type | Detected By | Example |
|---|---|---|
| Semantic contradiction | Qwen 2.5 7B | BRD: "email within 2 min" vs US: "within 60 min" |
| Missing downstream link | Python | PRD feature with no Epic |
| Orphaned requirement | Python | Epic linking to non-existent PRD |
| No acceptance criteria | Python | User Story with untestable requirement |
| Version mismatch | Python | SDD written against outdated BRD version |
| Coverage gap | Python + AI | BRD requirement not addressed in CDD |

---

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| AI Model | Qwen 2.5 7B (Q4_K_M, ~4.7GB) | Semantic conflict analysis |
| Local Inference | Ollama | GPU-accelerated inference server |
| Backend | FastAPI + asyncio | API server, SSE streaming |
| Graph Engine | NetworkX | Traceability dependency graph |
| Document Parsing | python-frontmatter + regex | Markdown + YAML frontmatter extraction |
| Frontend | Vanilla HTML/CSS/JS | Premium dark UI, no framework |
| Realtime Updates | Server-Sent Events (SSE) | Live pipeline progress streaming |

---

## Hardware Requirements

Tested on RTX 3070 Ti (8GB VRAM). Minimum recommended: 6GB VRAM.

The Q4_K_M quantized model uses ~4.7GB VRAM, leaving ~3GB for context window (KV cache).

---

## Setup

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.com) installed and running
- RTX GPU with 6GB+ VRAM (or CPU with 16GB+ RAM, slower)

### 1. Pull the model
```bash
ollama pull qwen2.5:7b
```

### 2. Clone and set up
```bash
git clone https://github.com/cooldip00/traceiq.git
cd traceiq

python -m venv venv
.\venv\Scripts\activate          # Windows
# source venv/bin/activate       # Linux/Mac

pip install -r requirements.txt
```

### 3. Run
```bash
python -m uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000** in your browser.

---

## Usage

1. Upload your PDM documents as `.md` files (drag & drop or browse)
2. Click **Run Traceability Analysis**
3. Watch the 5-step pipeline stream live in the browser
4. Review the conflict report — 🔴 Conflicts, 🟡 Warnings, 🟢 Consistent

### Sample Documents

The `sample_docs/` directory contains a complete fictional **Customer Loyalty Program** initiative across all 9 document types, with 5 pre-seeded conflicts including a semantic SLA mismatch detected by the AI.

```bash
# Upload everything in sample_docs/confluence/ and sample_docs/jira/
# to see the full demo
```

---

## Document Format

Each markdown document requires a YAML frontmatter block:

```markdown
---
doc_id: BRD-001
doc_type: BRD
title: Customer Loyalty Program
version: "1.2"
author: Jane Smith
date: 2025-01-20
links_to:
  - CDD-001
  - PRD-001
---

## Requirements

### R-01
**Title:** User Registration
**Priority:** High
**Description:** The system must allow customers to register...
**Acceptance Criteria:**
- AC-01: Registration available on web and mobile
**Traces To:** CDD-001/C-01, PRD-001/F-01
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Frontend (HTML/CSS/JS)                 │
│   Upload Zone │ Live Pipeline Feed │ Conflict Report │
└──────────────────────┬──────────────────────────────┘
                       │ POST /analyze
                       │ GET  /stream/{job_id} (SSE)
┌──────────────────────▼──────────────────────────────┐
│                 Backend (FastAPI)                   │
│                                                     │
│  Parser → Graph Builder → Validator → AI Analyzer  │
│                    → Report Generator               │
│                                                     │
│  NetworkX Graph    │    Qwen 2.5 7B via Ollama      │
│  (structural)      │    (semantic analysis only)    │
└─────────────────────────────────────────────────────┘
```

**Key design decision:** The LLM only handles semantic analysis of pre-filtered requirement pairs. All graph traversal, change detection, and structural validation is deterministic Python — making the system reliable and auditable.

---

## Project Structure

```
pdm-traceability-agent/
├── backend/
│   ├── main.py           # FastAPI app, SSE endpoints
│   ├── orchestrator.py   # Async 5-step pipeline
│   ├── parser.py         # Markdown + frontmatter parser
│   ├── graph_builder.py  # NetworkX traceability graph
│   ├── validator.py      # Structural validation (pure Python)
│   ├── analyzer.py       # Qwen AI semantic analysis
│   ├── reporter.py       # Report generation
│   └── models.py         # Pydantic data models
├── frontend/
│   ├── index.html        # Single-page app
│   ├── style.css         # Dark design system
│   └── app.js            # Upload, SSE, report rendering
├── sample_docs/
│   ├── confluence/       # LVC, TIA, BRD, CDD, PRD, SDD, ADD
│   └── jira/             # Epics, User Stories
└── requirements.txt
```

---

## License

MIT
