<div align="center">
  <h1>⚖️ Legal AI Copilot</h1>
  <p><strong>An intelligent legal document analysis platform powered by a multi-tool RAG agent.</strong></p>
</div>

<br>

Chat seamlessly with securely embedded corporate contracts. This platform goes beyond simple keyword matching, using an autonomous agentic router to decide when to extract financial tables, execute logical validity checks, or synthesize broad term summaries.

## ✨ Features

- **Agentic Routing**: LLM-driven classification categorizes your queries into extraction, logical reasoning, or search paths.
- **Hybrid Search Context**: Combines dense vector retrieval (BGE-M3) with sparse keyword retrieval (BM25) via Qdrant for pinpoint accuracy.
- **Asynchronous Document Processing**: Celery workers handle heavy OCR, chunking, and embedding pipelines in the background.
- **Multi-tenant Sessions**: Create secure chat workspaces bound to specific documents.

---

## 🏗️ Architecture Stack

| Layer | Technology | Why we chose it |
|---|---|---|
| **Reasoning Engine** | Qwen 3 (32B) via Groq | Blazing fast inference, excellent CoT deduction |
| **Embeddings** | BGE-M3 via OpenRouter | High-dimensional dense vectors fine-tuned for retrieval |
| **Vector DB** | Qdrant Cloud | Top-tier hybrid search (Dense + Sparse/BM25) |
| **Relational DB** | PostgreSQL (asyncpg) | Acid-compliant metadata and file tracking |
| **Task Queue** | Celery + Upstash Redis | Resilient background processing for large PDFs |
| **API Backend** | FastAPI | High-performance async Python backend |
| **Frontend UI** | Streamlit | Rapid, attractive conversational UI |

---

## 📁 Project Structure

```text
legal_rag/
├── app/            # FastAPI backend (Database, Models, Routers, Services)
├── frontend/       # Streamlit UI (app.py)
├── scripts/        # Admin utilities (db wipe, audit tools - DO NOT DEPLOY)
└── tests/          # Test suite (e2e, unit, stress tests)
```

---

## 🚀 Getting Started

### 1. Environment Setup

Clone the repo, create a virtual environment, and install requirements:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set up your secrets using the template:

```bash
cp .env.example .env
```
*(Open `.env` and fill in your Groq, OpenRouter, Postgres, Redis, and Qdrant credentials)*

### 2. Start the Services

In a production or local environment, you need to spin up the backend, the background workers, and the frontend. **Open separate terminal windows for each command** (ensure your virtual environment is activated in each):

**Terminal 1: FastAPI Backend**
```bash
uvicorn main:app --reload --env-file .env
```
*API docs available at `http://localhost:8000/docs`*

**Terminal 2: Celery Worker (PDF Processor)**
```bash
celery -A app.worker.celery_app worker -Q default --loglevel=info
```

**Terminal 3: Celery Worker (OCR Processor)**
```bash
celery -A app.worker.celery_app worker -Q ocr --loglevel=info
```

**Terminal 4: Streamlit UI**
```bash
streamlit run frontend/app.py
```
*UI available at `[https://legal-ai-copilot-xi.vercel.app/login]`*

---

## 🔌 Core API Endpoints

Once the FastAPI server is running, the core RAG workflows are fully accessible via REST:

| Method | Path | Description |
|---|---|---|
| `POST` | `/files/upload` | Upload a PDF. Returns immediately while Celery processes in background. |
| `POST` | `/session/create` | Start a chat session tied to specific files. |
| `POST` | `/ask-agent` | Send a query to the agentic router (Triggering CoT, extraction, or search). |
| `POST` | `/ask` | Fallback simple RAG endpoint for direct context synthesis. |
| `DELETE` | `/session/{id}` | Terminate an active session and clear context. |
