import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from auth import hash_password
from database import engine, init_db
from models import Exercise, User
from routers import auth_router, nutrition_router, workouts_router


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

        if not session.exec(select(User)).first():
            init_user = os.getenv("APP_USERNAME", "admin").lower().strip()
            init_pass = os.getenv("APP_PASSWORD", "changeme123")
            session.add(User(username=init_user, hashed_password=hash_password(init_pass)))
            session.commit()
    yield


app = FastAPI(title="Workout & Nutrition Tracker", lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(workouts_router.router)
app.include_router(nutrition_router.router)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def serve_ui():
    return FileResponse(INDEX_HTML)