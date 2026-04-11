# main.py
# Entry point for the Smart Dataset Explainer backend API.
# Supports: PRD #1 (upload), #2 (summary), #3 (Q&A), #4 (cleaning), #5 (ML), #7 (export), #8 (BYOK)
# Key deps: FastAPI (routing), session.py (in-memory state), executor.py (sandboxed code runs)
#
# Routes are added incrementally per the implementation plan. Only health check is wired here;
# upload, chat, clean, export routes are added in later steps.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

FRONTEND_DEV_ORIGIN = "http://localhost:5173"

app = FastAPI(title="Smart Dataset Explainer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_DEV_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
