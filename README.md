# AI Holding

AI-powered holding company with multi-agent management system.

## Architecture

```
Owner (You) ←→ Dashboard (Next.js) ←→ Backend API (FastAPI + WebSocket)
                                          ↓
                                    CEO Agent (LangGraph)
                                    ↙       ↓        ↘
                              Company 1   Company 2   Company N
                              ↙   ↓   ↘
                          Researcher  Analyst  Risk Manager
                                          ↓
                                    Vertex AI (LiteLLM Router)
```

## Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials
2. Run with Docker Compose:

```bash
cp .env.example .env
# Edit .env with your Vertex AI credentials
docker compose up --build
```

3. Open the dashboard: http://localhost (or http://localhost:3000 for local dev)

## Development

### Backend only (local Python):
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend only:
```bash
cd frontend
npm install
npm run dev
```

## Tech Stack

- **Backend**: Python / FastAPI / LangGraph / LiteLLM
- **Frontend**: Next.js 14 / TailwindCSS
- **Database**: PostgreSQL
- **Cache/Queue**: Redis
- **LLM**: Vertex AI (Gemini, DeepSeek, Claude)
- **Deploy**: Docker Compose
