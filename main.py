from fastapi import FastAPI

from app.database import Base, engine
from app.routers import agent_query, session
from app.routers import injest, query

app = FastAPI(title="Legal RAG API", version="2.0.0")

app.include_router(injest.router)
app.include_router(query.router)
app.include_router(agent_query.router)
app.include_router(session.router)


@app.on_event("startup")
async def startup():
    """
    Try to create DB tables on startup.
    Non-fatal — if the DB is unreachable the app still starts,
    and the error will surface on the first actual DB request.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables ready.")
    except Exception as e:
        print(f"⚠️  Could not connect to database at startup: {e}")
        print("   The app will still start. DB errors will surface per-request.")
