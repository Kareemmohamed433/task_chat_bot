"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.config import settings
from app.seed import init_db

# Configure application logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai_task_assistant")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    logger.info("Initializing database and seeding baseline records...")
    init_db()
    logger.info("AI Task Assistant service started successfully.")
    yield
    logger.info("AI Task Assistant shutting down...")


app = FastAPI(
    title="AI Task Assistant — TASK AI-ENG-02",
    description="Arabic AI-powered task management assistant with RBAC authorization, scheduling, and policy Q&A.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for standard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API endpoints
app.include_router(health_router)
app.include_router(chat_router)


@app.get("/", tags=["Root"])
def root():
    return {
        "service": "AI Task Assistant — TASK AI-ENG-02",
        "status": "operational",
        "docs_url": "/docs",
        "health_url": "/health",
    }
