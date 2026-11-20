from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from database import engine, init_db
from models import Exercise
from routers.auth_router import router as auth_router
from routers.nutrition_router import router as nutrition_router
from routers.weight_router import router as weight_router
from routers.workouts_router import router as workouts_router

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        if not session.exec(select(Exercise)).first():
            default_exercises = [
                Exercise(name="Barbell Squat", primary_muscle="Quads", equipment="Barbell"),
                Exercise(name="Deadlift", primary_muscle="Hamstrings/Back", equipment="Barbell"),
                Exercise(name="Incline Bench Press", primary_muscle="Upper Chest", equipment="Barbell"),
                Exercise(name="Romanian Deadlift (RDL)", primary_muscle="Hamstrings", equipment="Barbell"),
                Exercise(name="Dumbbell Lateral Raise", primary_muscle="Shoulders", equipment="Dumbbell"),
                Exercise(name="Lat Pulldown", primary_muscle="Back", equipment="Cable"),
                Exercise(name="Triceps Rope Pushdown", primary_muscle="Triceps", equipment="Cable"),
            ]
            session.add_all(default_exercises)
            session.commit()
    yield


app = FastAPI(title="Workout & Nutrition Tracker", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(workouts_router)
app.include_router(nutrition_router)
app.include_router(weight_router)


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse(INDEX_HTML)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)