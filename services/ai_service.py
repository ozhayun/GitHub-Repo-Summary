"""
Nebius Token Factory API integration.
Uses OpenAI SDK configured for Nebius base URL.
"""

import json
import os
import re
from typing import Any, Optional

from openai import OpenAI

from utils.processor import ProcessedRepo

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"


class AIServiceError(Exception):
    pass


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:].strip()


def _repair_truncated(s: str, max_chars: int = 1500) -> Optional[str]:
    m = re.search(r'"summary"\s*:\s*"', s, re.IGNORECASE)
    if not m:
        return None
    prefix, value = s[: m.end()], s[m.end() :]
    if len(value) <= max_chars:
        return None
    chunk = value[:max_chars]
    for sep in (". ", ".", " "):
        pos = chunk.rfind(sep)
        if pos > 80:
            chunk = chunk[: pos + len(sep)].rstrip()
            break
    escaped = chunk.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return prefix + escaped + '", "technologies": [], "structure": "Response truncated."}'


SYSTEM_PROMPT = """You are an expert at analyzing software repositories.

Follow this process:
1. First, identify the tech stack from config files (package.json, pyproject.toml, requirements.txt, Cargo.toml, go.mod, etc.). Extract language, frameworks, and key dependencies.
2. Review the directory tree and README to understand project structure and purpose.
3. Synthesize into a final JSON response.

Return a strictly formatted JSON object with exactly these keys:
- summary: Human-readable description of what the project does (markdown allowed). 3-6 sentences.
- technologies: Array of strings - main technologies, languages, frameworks (from your config analysis).
- structure: Brief description of project layout, main folders, entry points.

Output ONLY valid JSON. No markdown code fences, no explanation."""


def summarize(processed: ProcessedRepo, github_url: str) -> dict[str, Any]:
    """Call Nebius API and return {summary, technologies, structure}."""
    api_key = os.getenv("NEBIUS_API_KEY")
    if not api_key:
        raise AIServiceError("NEBIUS_API_KEY environment variable is not set.")

    model = DEFAULT_MODEL
    user_content = f"Repository: {github_url}\n\n{processed.directory_tree}\n\n{processed.file_contents}"

    client = OpenAI(api_key=api_key, base_url=NEBIUS_BASE_URL)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        err_str = str(e)
        msg = err_str.lower()
        if "401" in err_str or "authentication" in msg or "api_key" in msg:
            raise AIServiceError("Invalid or missing Nebius API key.")
        if "429" in err_str or "rate" in msg:
            raise AIServiceError("Rate limit exceeded. Try again later.")
        raise AIServiceError(f"Nebius API failed: {err_str[:300]}") from e

    choice = response.choices[0] if response.choices else None
    if not choice or not choice.message or not choice.message.content:
        raise AIServiceError("Empty response from Nebius API.")

    raw = choice.message.content.strip()
    json_str = _extract_json(raw)
    if not json_str:
        raise AIServiceError("LLM returned no JSON.")

    out = None
    try:
        out = json.loads(json_str)
    except json.JSONDecodeError as e:
        if "Unterminated string" in str(e):
            repaired = _repair_truncated(json_str)
            if repaired:
                try:
                    out = json.loads(repaired)
                except json.JSONDecodeError:
                    pass
        if out is None:
            raise AIServiceError(f"Invalid JSON from LLM: {e}") from e
    tech = out.get("technologies", [])
    if not isinstance(tech, list):
        tech = [str(t) for t in (tech or [])]
    return {
        "summary": out.get("summary", ""),
        "technologies": tech,
        "structure": out.get("structure", ""),
    }
