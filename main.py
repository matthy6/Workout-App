from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse

from auth import hash_password
from database import init_db
from routers.auth_router import router as auth_router
from routers.nutrition_router import router as nutrition_router
from routers.workouts_router import router as workouts_router

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Workout & Nutrition Tracker", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(workouts_router)
app.include_router(nutrition_router)


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(INDEX_HTML)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)