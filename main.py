"""
FastAPI service for GitHub repository summarization.
Uses Nebius Token Factory API for LLM inference.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator

from utils.github_client import GitHubClientError, RepoNotFoundError, clone_repo, cleanup
from utils.processor import process_repo
from services.ai_service import AIServiceError, summarize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GitHub Repo Summary",
    description="Summarize GitHub repositories using Nebius AI.",
    version="1.0.0",
)


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "message": message},
    )


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning("HTTP error %s: %s", exc.status_code, exc.detail)
    return _error_response(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    msg = exc.errors()[0].get("msg", "Invalid request") if exc.errors() else "Invalid request"
    return _error_response(400, msg)


class SummarizeRequest(BaseModel):
    github_url: str = Field(..., description="URL of a public GitHub repository")

    @field_validator("github_url")
    @classmethod
    def must_be_github(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("github_url is required")
        if "github.com" not in v:
            raise ValueError("URL must be a GitHub repository URL (e.g. https://github.com/owner/repo)")
        return v


class SummarizeResponse(BaseModel):
    summary: str
    technologies: list[str]
    structure: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
def summarize_endpoint(request: SummarizeRequest) -> SummarizeResponse:
    if not os.getenv("NEBIUS_API_KEY"):
        logger.error("NEBIUS_API_KEY not set")
        raise HTTPException(
            status_code=503,
            detail="Service misconfiguration: NEBIUS_API_KEY is not set.",
        )

    logger.info("Summarizing repo: %s", request.github_url)
    repo_path = None

    try:
        repo_path = clone_repo(request.github_url)
        logger.info("Cloned to %s", repo_path)
        processed = process_repo(repo_path)
        result = summarize(processed, request.github_url)
        logger.info("Summary generated successfully")
        return SummarizeResponse(
            summary=result["summary"],
            technologies=result["technologies"],
            structure=result["structure"],
        )
    except RepoNotFoundError as e:
        logger.warning("Repo not found: %s", e)
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GitHubClientError as e:
        logger.warning("GitHub error: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except AIServiceError as e:
        logger.warning("AI service error: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if repo_path is not None:
            cleanup(repo_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
