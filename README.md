# GitHub Repo Summary

A FastAPI service that summarizes GitHub repositories using the [Nebius Token Factory](https://nebius.com/ai-studio) API. Given a GitHub URL, it clones the repo, filters and prioritizes files, and uses an LLM to produce a structured summary with technologies and structure.

## Project Structure

```
github_repo_summary/
├── main.py                 # FastAPI app, POST /summarize, exception handlers
├── requirements.txt
├── README.md
├── utils/
│   ├── github_client.py    # Shallow clone via git
│   └── processor.py        # Recursive walker, filtering, context trimming
└── services/
    └── ai_service.py       # Nebius API integration
```

## Requirements

- Python 3.10+
- `git` on PATH
- Nebius API key ([Nebius Token Factory](https://nebius.com/ai-studio))

## Setup

1. **Create virtual environment and install dependencies:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set the API key:**

   ```bash
   export NEBIUS_API_KEY=your-api-key
   ```

   Or create a `.env` file with `NEBIUS_API_KEY=your-api-key`.

3. **Run the server:**

   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

4. **Test the endpoint:**

   ```bash
   curl -X POST http://localhost:8000/summarize \
     -H "Content-Type: application/json" \
     -d '{"github_url": "https://github.com/psf/requests"}'
   ```

## Model Choice

The service uses the Meta-Llama-3.1-8B-Instruct model**. It balances speed, cost, and quality and follows structured output instructions reliably.

## Repo Processing Strategy

### Overview

Repositories can be large; we cannot send everything to the LLM. The processor uses a recursive file walker to filter junk, prioritize key files, and trim content to stay within a 6,000-token budget using tiktoken.

### What We Skip

**Directories (heavy/junk):** `node_modules`, `.git`, `__pycache__`, `venv`, `.venv`, `env`, `.env`, `dist`, `build`, `.next`, `.nuxt`, `.cache`, `coverage`, `vendor`, `bower_components`, and tool caches.

**Files:**
- Lock files: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `Pipfile.lock`, `composer.lock`, `Cargo.lock`, `go.sum`
- Binaries: images, videos, executables (`.png`, `.jpg`, `.pdf`, `.zip`, `.exe`, `.so`, `.dll`, `.pyc`, etc.)
- Hidden files and directories (names starting with `.`)

### What We Prioritize

1. **Always first:** README.md, LICENSE, package.json, pyproject.toml, requirements.txt
2. **Entry points:** main.py, app.py, app.js, index.js, index.ts, main.ts
3. **Config files:** setup.py, Cargo.toml, go.mod, Makefile, Dockerfile, tsconfig.json, etc.
4. **Other source:** Remaining text files by depth (shallower = higher priority)

### Context Management (6,000 Token Limit)

1. **Directory tree:** A full text-based tree is built first to give the LLM the "big picture."
2. **Token counting:** We use `tiktoken` (cl100k_base) to count tokens. If tiktoken is unavailable, we fall back to chars/4.
3. **Intelligent truncation:** When total content exceeds the budget:
   - We fill the budget in priority order (README, LICENSE, configs, entry points, then other files).
   - For individual files that exceed their share of the budget, we keep the **beginning and end** and insert `... [truncated N tokens] ...` in the middle. This preserves imports, function signatures, and closing logic.
4. **Sliding budget:** Each file consumes from a remaining token budget. Less important files are skipped or truncated first.

### Error Handling

On error, the API returns `{"status": "error", "message": "..."}` with an appropriate HTTP status:

- **400** – Invalid request (non-GitHub URL, malformed body)
- **404** – Repository not found or access denied (private repo)
- **503** – `NEBIUS_API_KEY` not set
- **502** – Nebius API failure, rate limit, or invalid response
- **500** – Unexpected server error

---

## Key Decisions

### Why We Filter Lock Files

Lock files (`package-lock.json`, `yarn.lock`, `poetry.lock`, etc.) contain exact dependency versions and hashes. They are large (often 10k+ lines), machine-generated, and add little signal for understanding *what* the project does. Including them would:
- Consume most of our 6,000-token budget
- Drown out README, configs, and source code
- Reduce summary accuracy

We exclude them to maximize the **signal-to-noise ratio** for the LLM.

### Why Meta-Llama-3.1-8B-Instruct

We chose **meta-llama/Meta-Llama-3.1-8B-Instruct** because it:
- Follows structured output instructions reliably (JSON format)
- Stays within token limits when prompted correctly
- Balances speed and cost for a summarization workload
- Is widely available on Nebius Token Factory
