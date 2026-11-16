import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select

load_dotenv()

# --- 1. Database Configuration ---

DATABASE_URL = (
    os.getenv("DATABASE_URL_POOLED")
    or os.getenv("DATABASE_URL")
    or "sqlite:///./workout.db"
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(
    DATABASE_URL, echo=False, connect_args=connect_args, pool_pre_ping=True
)


def get_session():
    with Session(engine) as session:
        yield session


# --- 2. Password Hashing Helpers ---


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()
    return f"{salt}${digest}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
        return secrets.compare_digest(hash_password(plain_password, salt), stored_hash)
    except ValueError:
        return False


# --- 3. Database Models ---


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    auth_token: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    workouts: List["WorkoutSession"] = Relationship(back_populates="user")
    templates: List["WorkoutTemplate"] = Relationship(back_populates="user")
    food_logs: List["FoodLog"] = Relationship(back_populates="user")
    saved_foods: List["FoodDatabase"] = Relationship(back_populates="user")


class Exercise(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    primary_muscle: str
    equipment: str


class WorkoutSession(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    title: str
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    notes: Optional[str] = None

    user: Optional[User] = Relationship(back_populates="workouts")
    sets: List["SetLog"] = Relationship(
        back_populates="workout",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SetLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workout_id: UUID = Field(foreign_key="workoutsession.id", index=True)
    exercise_name: str
    set_number: int
    weight: float
    reps: int
    rpe: Optional[float] = None
    notes: Optional[str] = None

    workout: Optional[WorkoutSession] = Relationship(back_populates="sets")


class WorkoutTemplate(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    name: str
    notes: Optional[str] = None

    user: Optional[User] = Relationship(back_populates="templates")
    sets: List["TemplateSet"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TemplateSet(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    template_id: UUID = Field(foreign_key="workouttemplate.id", index=True)
    exercise_name: str
    set_number: int
    weight: Optional[float] = None
    reps: Optional[int] = None
    rpe: Optional[float] = None
    notes: Optional[str] = None

    template: Optional[WorkoutTemplate] = Relationship(back_populates="sets")


class FoodLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    date: str = Field(index=True)  # YYYY-MM-DD format
    food_name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    calories: int
    protein: Optional[float] = 0.0
    carbs: Optional[float] = 0.0
    fats: Optional[float] = 0.0

    user: Optional[User] = Relationship(back_populates="food_logs")


class FoodDatabase(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    food_name: str = Field(index=True)
    default_unit: Optional[str] = None
    calories_per_unit: float
    protein_per_unit: float
    carbs_per_unit: float
    fats_per_unit: float

    user: Optional[User] = Relationship(back_populates="saved_foods")


# --- 4. Token Authentication Dependency ---


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in",
        )
    token = authorization.replace("Bearer ", "", 1).strip()
    user = session.exec(select(User).where(User.auth_token == token)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
    return user


# --- 5. App Lifecycle & Seed Data ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
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

# --- 6. API Endpoints ---


class AuthPayload(SQLModel):
    username: str
    password: str


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: AuthPayload, session: Session = Depends(get_session)):
    clean_username = payload.username.lower().strip()
    if len(clean_username) < 3 or len(payload.password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Username must be at least 3 chars and password at least 6 chars.",
        )
    if session.exec(select(User).where(User.username == clean_username)).first():
        raise HTTPException(status_code=400, detail="Username already taken.")

    token = secrets.token_hex(32)
    new_user = User(
        username=clean_username,
        hashed_password=hash_password(payload.password),
        auth_token=token,
    )
    session.add(new_user)
    session.commit()
    return {"token": token, "username": clean_username}


@app.post("/api/login")
def login_user(payload: AuthPayload, session: Session = Depends(get_session)):
    clean_username = payload.username.lower().strip()
    user = session.exec(select(User).where(User.username == clean_username)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = secrets.token_hex(32)
    user.auth_token = token
    session.add(user)
    session.commit()
    return {"token": token, "username": user.username}


@app.get("/api/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"id": str(current_user.id), "username": current_user.username}


@app.get("/api/exercises")
def get_exercises(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    default_names = [e.name for e in session.exec(select(Exercise)).all()]

    user_logged_names = session.exec(
        select(SetLog.exercise_name)
        .join(WorkoutSession)
        .where(WorkoutSession.user_id == current_user.id)
        .distinct()
    ).all()

    user_template_names = session.exec(
        select(TemplateSet.exercise_name)
        .join(WorkoutTemplate)
        .where(WorkoutTemplate.user_id == current_user.id)
        .distinct()
    ).all()

    seen = set()
    combined = []
    for name in default_names + list(user_logged_names) + list(user_template_names):
        clean = name.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            combined.append({"name": clean})

    return sorted(combined, key=lambda x: x["name"].lower())


@app.get("/api/exercises/history")
def get_exercise_history(
    name: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    clean_target = name.strip().lower()
    if not clean_target:
        return []

    sets = session.exec(
        select(SetLog, WorkoutSession)
        .join(WorkoutSession)
        .where(WorkoutSession.user_id == current_user.id)
        .order_by(WorkoutSession.date.desc())
    ).all()

    history_map = {}
    for set_log, workout in sets:
        if set_log.exercise_name.strip().lower() == clean_target:
            if workout.id not in history_map:
                history_map[workout.id] = {
                    "workout_title": workout.title,
                    "date": workout.date.strftime("%Y-%m-%d"),
                    "started_at": workout.started_at.isoformat() if workout.started_at else None,
                    "sets": []
                }
            history_map[workout.id]["sets"].append({
                "set_number": set_log.set_number,
                "weight": set_log.weight,
                "reps": set_log.reps,
                "rpe": set_log.rpe,
                "notes": set_log.notes
            })

    results = []
    for w_data in history_map.values():
        w_data["sets"] = sorted(w_data["sets"], key=lambda x: x["set_number"])
        results.append(w_data)

    return results


@app.get("/api/nutrition/foods")
def get_saved_foods(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    foods = session.exec(
        select(FoodDatabase)
        .where(FoodDatabase.user_id == current_user.id)
        .order_by(FoodDatabase.food_name.asc())
    ).all()
    return [
        {
            "food_name": f.food_name,
            "unit": f.default_unit,
            "calories_per_unit": f.calories_per_unit,
            "protein_per_unit": f.protein_per_unit,
            "carbs_per_unit": f.carbs_per_unit,
            "fats_per_unit": f.fats_per_unit,
        }
        for f in foods
    ]


class FoodLogCreate(SQLModel):
    date: str
    food_name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    calories: int
    protein: Optional[float] = 0.0
    carbs: Optional[float] = 0.0
    fats: Optional[float] = 0.0


@app.post("/api/nutrition", status_code=status.HTTP_201_CREATED)
def log_food(
    payload: FoodLogCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    clean_name = payload.food_name.strip()
    
    food = FoodLog(
        user_id=current_user.id,
        date=payload.date,
        food_name=clean_name,
        amount=payload.amount,
        unit=payload.unit.strip() if payload.unit else None,
        calories=payload.calories,
        protein=payload.protein,
        carbs=payload.carbs,
        fats=payload.fats,
    )
    session.add(food)

    if payload.amount and payload.amount > 0:
        amt = payload.amount
        existing_db = session.exec(
            select(FoodDatabase).where(
                FoodDatabase.user_id == current_user.id,
                FoodDatabase.food_name.ilike(clean_name)
            )
        ).first()

        c_per_u = payload.calories / amt
        p_per_u = (payload.protein or 0) / amt
        carb_per_u = (payload.carbs or 0) / amt
        f_per_u = (payload.fats or 0) / amt

        if existing_db:
            existing_db.default_unit = payload.unit.strip() if payload.unit else existing_db.default_unit
            existing_db.calories_per_unit = c_per_u
            existing_db.protein_per_unit = p_per_u
            existing_db.carbs_per_unit = carb_per_u
            existing_db.fats_per_unit = f_per_u
            session.add(existing_db)
        else:
            new_db = FoodDatabase(
                user_id=current_user.id,
                food_name=clean_name,
                default_unit=payload.unit.strip() if payload.unit else None,
                calories_per_unit=c_per_u,
                protein_per_unit=p_per_u,
                carbs_per_unit=carb_per_u,
                fats_per_unit=f_per_u,
            )
            session.add(new_db)

    session.commit()
    return {"status": "logged"}


@app.get("/api/nutrition")
def get_food_logs(
    date: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    logs = session.exec(
        select(FoodLog)
        .where(FoodLog.user_id == current_user.id, FoodLog.date == date)
    ).all()

    total_calories = sum(l.calories for l in logs)
    total_protein = sum(l.protein or 0 for l in logs)
    total_carbs = sum(l.carbs or 0 for l in logs)
    total_fats = sum(l.fats or 0 for l in logs)

    return {
        "date": date,
        "totals": {
            "calories": total_calories,
            "protein": round(total_protein, 1),
            "carbs": round(total_carbs, 1),
            "fats": round(total_fats, 1),
        },
        "items": [
            {
                "id": str(item.id),
                "food_name": item.food_name,
                "amount": item.amount,
                "unit": item.unit,
                "calories": item.calories,
                "protein": item.protein,
                "carbs": item.carbs,
                "fats": item.fats,
            }
            for item in logs
        ],
    }


@app.get("/api/nutrition/history")
def get_all_nutrition_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    logs = session.exec(
        select(FoodLog)
        .where(FoodLog.user_id == current_user.id)
        .order_by(FoodLog.date.desc())
    ).all()

    history_map = {}
    for l in logs:
        if l.date not in history_map:
            history_map[l.date] = {
                "date": l.date,
                "items": []
            }
        history_map[l.date]["items"].append({
            "id": str(l.id),
            "food_name": l.food_name,
            "amount": l.amount,
            "unit": l.unit,
            "calories": l.calories,
            "protein": l.protein,
            "carbs": l.carbs,
            "fats": l.fats
        })

    results = []
    for d_data in history_map.values():
        total_cals = sum(i["calories"] for i in d_data["items"])
        total_pro = sum(i["protein"] or 0 for i in d_data["items"])
        total_carb = sum(i["carbs"] or 0 for i in d_data["items"])
        total_fat = sum(i["fats"] or 0 for i in d_data["items"])
        d_data["totals"] = {
            "calories": total_cals,
            "protein": round(total_pro, 1),
            "carbs": round(total_carb, 1),
            "fats": round(total_fat, 1)
        }
        results.append(d_data)

    return results


@app.delete("/api/nutrition/{food_id}")
def delete_food_log(
    food_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    item = session.exec(
        select(FoodLog).where(FoodLog.id == food_id, FoodLog.user_id == current_user.id)
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Food item not found.")
    session.delete(item)
    session.commit()
    return {"status": "deleted"}


# --- Template Endpoints ---


class TemplateSetInput(SQLModel):
    exercise_name: str
    set_number: int
    weight: Optional[float] = None
    reps: Optional[int] = None
    rpe: Optional[float] = None
    notes: Optional[str] = None


class TemplateCreate(SQLModel):
    name: str
    notes: Optional[str] = None
    sets: List[TemplateSetInput]


@app.post("/api/templates", status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Template name is required.")
    if not payload.sets:
        raise HTTPException(
            status_code=400, detail="Add at least one exercise row to save a template."
        )

    template = WorkoutTemplate(
        user_id=current_user.id,
        name=clean_name,
        notes=payload.notes.strip() if payload.notes else None,
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    for item in payload.sets:
        session.add(
            TemplateSet(
                template_id=template.id,
                exercise_name=item.exercise_name.strip(),
                set_number=item.set_number,
                weight=item.weight,
                reps=item.reps,
                rpe=item.rpe,
                notes=item.notes.strip() if item.notes else None,
            )
        )

    session.commit()
    return {"status": "created", "template_id": str(template.id)}


@app.get("/api/templates")
def list_templates(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    templates = session.exec(
        select(WorkoutTemplate)
        .where(WorkoutTemplate.user_id == current_user.id)
        .order_by(WorkoutTemplate.name.asc())
    ).all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "notes": t.notes,
            "sets": [
                {
                    "exercise_name": s.exercise_name,
                    "set_number": s.set_number,
                    "weight": s.weight,
                    "reps": s.reps,
                    "rpe": s.rpe,
                    "notes": s.notes,
                }
                for s in sorted(t.sets, key=lambda x: x.set_number)
            ],
        }
        for t in templates
    ]


@app.delete("/api/templates/{template_id}")
def delete_template(
    template_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    template = session.exec(
        select(WorkoutTemplate).where(
            WorkoutTemplate.id == template_id,
            WorkoutTemplate.user_id == current_user.id,
        )
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found.")

    session.delete(template)
    session.commit()
    return {"status": "deleted"}


# --- Workout Endpoints ---


class SetInput(SQLModel):
    exercise_name: str
    set_number: int
    weight: float
    reps: int
    rpe: Optional[float] = None
    notes: Optional[str] = None


class WorkoutCreate(SQLModel):
    title: str
    notes: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    sets: List[SetInput]


@app.post("/api/workouts")
def save_workout(
    payload: WorkoutCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    end_time = payload.ended_at or now
    start_time = payload.started_at or end_time

    if end_time < start_time:
        raise HTTPException(
            status_code=400, detail="End time cannot be earlier than start time."
        )

    workout = WorkoutSession(
        user_id=current_user.id,
        title=payload.title,
        date=start_time,
        started_at=start_time,
        ended_at=end_time,
        notes=payload.notes,
    )
    session.add(workout)
    session.commit()
    session.refresh(workout)

    for item in payload.sets:
        clean_note = item.notes.strip() if item.notes else None
        session.add(
            SetLog(
                workout_id=workout.id,
                exercise_name=item.exercise_name,
                set_number=item.set_number,
                weight=item.weight,
                reps=item.reps,
                rpe=item.rpe,
                notes=clean_note or None,
            )
        )

    session.commit()
    return {"status": "saved", "workout_id": str(workout.id)}


@app.get("/api/workouts")
def list_workouts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workouts = session.exec(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == current_user.id)
        .order_by(WorkoutSession.date.desc())
    ).all()

    results = []
    for w in workouts:
        duration_mins = None
        if w.started_at and w.ended_at:
            duration_mins = max(1, round((w.ended_at - w.started_at).total_seconds() / 60))

        results.append(
            {
                "id": str(w.id),
                "title": w.title,
                "date": w.date.strftime("%Y-%m-%d"),
                "started_at": w.started_at.isoformat() if w.started_at else None,
                "ended_at": w.ended_at.isoformat() if w.ended_at else None,
                "duration_mins": duration_mins,
                "notes": w.notes,
                "sets": [
                    {
                        "exercise": s.exercise_name,
                        "set": s.set_number,
                        "weight": s.weight,
                        "reps": s.reps,
                        "rpe": s.rpe,
                        "notes": s.notes,
                    }
                    for s in sorted(w.sets, key=lambda x: x.set_number)
                ],
            }
        )
    return results


# --- 7. Web Interface with Grouped Exercise Cards ---


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Workout & Nutrition Tracker</title>
      <style>
        :root { --bg: #121212; --card: #1e1e1e; --accent: #2563eb; --green: #16a34a; --text: #f3f4f6; --muted: #9ca3af; --border: #333; }
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); margin: 0; padding: 16px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 600px; display: flex; flex-direction: column; gap: 16px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
        .header-bar { display: flex; justify-content: space-between; align-items: center; }
        h1, h2, h3 { margin: 0 0 12px 0; }
        label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; }
        input, select, textarea, button { width: 100%; padding: 10px; margin-bottom: 8px; border-radius: 6px; border: 1px solid var(--border); background: #2a2a2a; color: var(--text); font-size: 15px; }
        button { background: var(--accent); color: white; border: none; font-weight: 600; cursor: pointer; }
        button:hover { opacity: 0.9; }
        .tab-bar { display: grid; grid-template-columns: repeat(5, 1fr); gap: 3px; background: var(--card); padding: 6px; border-radius: 10px; border: 1px solid var(--border); }
        .tab-btn { margin: 0; padding: 8px 1px; font-size: 11px; background: transparent; color: var(--muted); border-radius: 7px; text-align: center; transition: background 0.15s, color 0.15s; }
        .tab-btn.active { background: var(--accent); color: white; }
        .template-bar { display: grid; grid-template-columns: 1fr auto auto; gap: 6px; align-items: center; background: #262626; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border); }
        .template-bar select { margin: 0; font-size: 14px; padding: 8px; }
        .time-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
        .timer-bar { display: flex; justify-content: space-between; align-items: center; background: #262626; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border); }
        .exercise-card { background: #252525; border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 12px; }
        .exercise-header { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; margin-bottom: 10px; }
        .exercise-header input { margin: 0; font-weight: 600; background: #1e1e1e; border-color: #444; }
        .set-item { background: #1e1e1e; border: 1px solid #2e2e2e; border-radius: 8px; padding: 8px; margin-bottom: 8px; }
        .set-row { display: grid; grid-template-columns: 28px 1fr 1fr 1fr auto; gap: 6px; align-items: center; margin-bottom: 6px; }
        .set-num { font-size: 13px; font-weight: 700; color: var(--muted); text-align: center; background: #2a2a2a; padding: 8px 0; border-radius: 5px; }
        .set-row input { margin: 0; padding: 8px; font-size: 14px; }
        .set-note { margin: 0; padding: 6px 10px; font-size: 13px; background: #252525; }
        .btn-del { background: #dc2626; padding: 8px 11px; width: auto; margin: 0; }
        .btn-small { width: auto; padding: 8px 12px; font-size: 13px; background: #374151; margin: 0; white-space: nowrap; }
        .btn-add-set { width: 100%; padding: 7px; font-size: 13px; background: #333; color: #e5e7eb; margin: 0; border: 1px dashed #555; }
        .action-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
        .action-row button { margin: 0; }
        .history-item { border-bottom: 1px solid var(--border); padding: 12px 0; }
        .history-item:last-child { border: none; }
        .macro-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; text-align: center; margin-bottom: 12px; }
        .macro-box { background: #252525; padding: 8px; border-radius: 8px; border: 1px solid var(--border); }
        .macro-val { font-size: 16px; font-weight: 700; color: var(--text); }
        .macro-lbl { font-size: 11px; color: var(--muted); }
        .badge { font-size: 12px; background: #333; padding: 4px 8px; border-radius: 4px; color: var(--muted); }
        .hidden { display: none !important; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header-bar">
          <h1>🏋️ Workout & Diet</h1>
          <div id="user-controls" class="hidden">
            <span id="user-badge" class="badge" style="margin-right:6px;"></span>
            <button class="btn-small" onclick="logout()">Log Out</button>
          </div>
        </div>

        <!-- Login / Sign Up Screen -->
        <div id="auth-screen" class="card">
          <h2>Sign In</h2>
          <input type="text" id="auth-user" placeholder="Username">
          <input type="password" id="auth-pass" placeholder="Password">
          <button onclick="handleAuth('/api/login')">Log In</button>
          <button onclick="handleAuth('/api/register')" style="background: #374151;">Create New Account</button>
        </div>

        <!-- Main App Screen -->
        <div id="app-screen" class="hidden" style="display:flex; flex-direction:column; gap:16px;">
          
          <!-- Navigation Tabs -->
          <div class="tab-bar">
            <button type="button" id="tab-btn-log" class="tab-btn active" onclick="switchTab('log')">Log</button>
            <button type="button" id="tab-btn-nutrition" class="tab-btn" onclick="switchTab('nutrition')">Nutrition</button>
            <button type="button" id="tab-btn-nut-history" class="tab-btn" onclick="switchTab('nut-history')">Diet Hist</button>
            <button type="button" id="tab-btn-history" class="tab-btn" onclick="switchTab('history')">Workouts</button>
            <button type="button" id="tab-btn-exercises" class="tab-btn" onclick="switchTab('exercises')">Exercises</button>
          </div>

          <!-- Tab 1: Active Workout Logger -->
          <div id="tab-log" class="card">
            <h2>Log Session</h2>

            <!-- Template Loader -->
            <div class="template-bar">
              <select id="template-select">
                <option value="">-- Select a Workout Template --</option>
              </select>
              <button type="button" class="btn-small" style="background: var(--accent);" onclick="loadSelectedTemplate()">Load</button>
              <button type="button" class="btn-del" title="Delete selected template" onclick="deleteSelectedTemplate()">✕</button>
            </div>
            
            <!-- Live Session Timer -->
            <div class="timer-bar">
              <div>
                <span style="font-size: 12px; color: var(--muted);">Session Timer</span>
                <div id="live-timer" style="font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums;">00:00:00</div>
              </div>
              <button type="button" id="start-btn" class="btn-small" style="background: var(--green);" onclick="startSession()">▶ Start Now</button>
            </div>

            <input type="text" id="workout-title" placeholder="Workout Title (e.g. Leg Day, Push Day)" value="Workout Session">
            
            <!-- Start & End Time Inputs -->
            <div class="time-grid">
              <div>
                <label for="start-time">Start Time</label>
                <input type="datetime-local" id="start-time">
              </div>
              <div>
                <label for="end-time">End Time (blank = now)</label>
                <input type="datetime-local" id="end-time">
              </div>
            </div>

            <textarea id="workout-notes" placeholder="Overall session notes (optional)..." rows="2"></textarea>

            <h3>Exercises</h3>
            <datalist id="exercise-options"></datalist>
            <div id="exercises-container"></div>
            
            <div class="action-row">
              <button type="button" onclick="addExerciseBlock()" style="background: #374151;">+ Add Exercise</button>
              <button type="button" onclick="saveAsTemplate()" style="background: #4b5563;">📋 Save as Template</button>
            </div>
            <button type="button" onclick="submitWorkout()">Finish & Save Workout</button>
          </div>

          <!-- Tab 2: Nutrition & Calorie Tracker -->
          <div id="tab-nutrition" class="card hidden">
            <h2>Calorie & Macro Tracker</h2>
            
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
              <input type="date" id="nutrition-date" style="margin:0; width:auto;" onchange="loadNutrition()">
              <button type="button" class="btn-small" style="background:#374151;" onclick="openTDEECalculator()">🧮 Calorie Calculator</button>
            </div>

            <!-- Daily Totals Summary -->
            <div class="macro-grid">
              <div class="macro-box">
                <div class="macro-val" id="tot-cal">0</div>
                <div class="macro-lbl">Calories</div>
              </div>
              <div class="macro-box">
                <div class="macro-val" id="tot-pro">0g</div>
                <div class="macro-lbl">Protein</div>
              </div>
              <div class="macro-box">
                <div class="macro-val" id="tot-carb">0g</div>
                <div class="macro-lbl">Carbs</div>
              </div>
              <div class="macro-box">
                <div class="macro-val" id="tot-fat">0g</div>
                <div class="macro-lbl">Fats</div>
              </div>
            </div>

            <!-- Quick Add Meal Form -->
            <div style="background:#262626; padding:10px; border-radius:8px; border:1px solid var(--border); margin-bottom:12px;">
              <h3 style="margin-bottom:8px; font-size:14px;">Log Food / Meal</h3>
              
              <input type="text" id="food-name" list="food-catalog-options" placeholder="Food name (e.g. White Rice)" style="margin-bottom:6px;" oninput="onFoodNameInput()">
              <datalist id="food-catalog-options"></datalist>
              
              <!-- Amount & Unit Datalist Row -->
              <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px; margin-bottom:6px;">
                <input type="number" id="food-amount" placeholder="Amount (e.g. 150)" step="any" style="margin:0;" oninput="onFoodAmountInput()">
                <input type="text" id="food-unit" list="unit-options" placeholder="Unit (grams, oz, cups...)" style="margin:0;">
                <datalist id="unit-options">
                  <option value="grams">
                  <option value="oz">
                  <option value="cups">
                  <option value="scoops">
                  <option value="tbsp">
                  <option value="ml">
                  <option value="pieces">
                  <option value="servings">
                </datalist>
              </div>

              <div style="display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:6px; margin-bottom:6px;">
                <input type="number" id="food-cal" placeholder="Cals" style="margin:0;">
                <input type="number" id="food-pro" placeholder="Pro (g)" step="0.5" style="margin:0;">
                <input type="number" id="food-carb" placeholder="Carb (g)" step="0.5" style="margin:0;">
                <input type="number" id="food-fat" placeholder="Fat (g)" step="0.5" style="margin:0;">
              </div>
              <button type="button" onclick="addFoodItem()" style="background:var(--green); margin:0;">+ Add to Daily Log</button>
            </div>

            <h3>Logged Items Today</h3>
            <div id="nutrition-items-container">
              <p style="color: var(--muted)">No food logged for this date.</p>
            </div>
          </div>

          <!-- Tab 3: Nutrition History -->
          <div id="tab-nut-history" class="card hidden">
            <h2>Nutrition History</h2>
            <div id="nutrition-history-container">Loading diet history...</div>
          </div>

          <!-- Tab 4: Past Workouts History -->
          <div id="tab-history" class="card hidden">
            <h2>Past Workouts</h2>
            <div id="history-container">Loading past workouts...</div>
          </div>

          <!-- Tab 5: Exercise Performance History -->
          <div id="tab-exercises" class="card hidden">
            <h2>Exercise Progress</h2>
            <div style="display:flex; gap:6px; margin-bottom:12px;">
              <input type="text" id="exercise-search-input" list="exercise-options" placeholder="Select or type exercise..." style="margin:0;">
              <button type="button" class="btn-small" style="background: var(--accent); padding: 0 16px;" onclick="loadExerciseHistory()">View</button>
            </div>
            <div id="exercise-history-container">
              <p style="color: var(--muted)">Select an exercise above to view past performances.</p>
            </div>
          </div>

        </div>
      </div>

      <script>
        let exerciseCatalog = [];
        let savedTemplates = [];
        let savedFoodsCatalog = [];
        let timerInterval = null;

        function switchTab(tabName) {
          const logView = document.getElementById('tab-log');
          const nutView = document.getElementById('tab-nutrition');
          const nutHistView = document.getElementById('tab-nut-history');
          const historyView = document.getElementById('tab-history');
          const exercisesView = document.getElementById('tab-exercises');
          
          const logBtn = document.getElementById('tab-btn-log');
          const nutBtn = document.getElementById('tab-btn-nutrition');
          const nutHistBtn = document.getElementById('tab-btn-nut-history');
          const historyBtn = document.getElementById('tab-btn-history');
          const exercisesBtn = document.getElementById('tab-btn-exercises');

          logView.classList.add('hidden');
          nutView.classList.add('hidden');
          nutHistView.classList.add('hidden');
          historyView.classList.add('hidden');
          exercisesView.classList.add('hidden');
          logBtn.classList.remove('active');
          nutBtn.classList.remove('active');
          nutHistBtn.classList.remove('active');
          historyBtn.classList.remove('active');
          exercisesBtn.classList.remove('active');

          if (tabName === 'nutrition') {
            nutView.classList.remove('hidden');
            nutBtn.classList.add('active');
            loadNutrition();
            refreshSavedFoods();
          } else if (tabName === 'nut-history') {
            nutHistView.classList.remove('hidden');
            nutHistBtn.classList.add('active');
            loadNutritionHistory();
          } else if (tabName === 'history') {
            historyView.classList.remove('hidden');
            historyBtn.classList.add('active');
            loadHistory();
          } else if (tabName === 'exercises') {
            exercisesView.classList.remove('hidden');
            exercisesBtn.classList.add('active');
          } else {
            logView.classList.remove('hidden');
            logBtn.classList.add('active');
          }
        }

        function toLocalDateString(dateObj) {
          const offset = dateObj.getTimezoneOffset();
          const local = new Date(dateObj.getTime() - offset * 60000);
          return local.toISOString().split('T')[0];
        }

        function toLocalDatetimeInput(dateObj) {
          const offset = dateObj.getTimezoneOffset();
          const local = new Date(dateObj.getTime() - offset * 60000);
          return local.toISOString().slice(0, 16);
        }

        function startSession() {
          const now = new Date();
          document.getElementById('start-time').value = toLocalDatetimeInput(now);
          document.getElementById('end-time').value = '';
          const btn = document.getElementById('start-btn');
          btn.innerText = '● Active';
          btn.style.background = '#d97706';

          if (timerInterval) clearInterval(timerInterval);
          timerInterval = setInterval(updateLiveTimer, 1000);
          updateLiveTimer();
        }

        function updateLiveTimer() {
          const startVal = document.getElementById('start-time').value;
          if (!startVal) return;
          const diffSecs = Math.max(0, Math.floor((Date.now() - new Date(startVal).getTime()) / 1000));
          const hrs = String(Math.floor(diffSecs / 3600)).padStart(2, '0');
          const mins = String(Math.floor((diffSecs % 3600) / 60)).padStart(2, '0');
          const secs = String(diffSecs % 60).padStart(2, '0');
          document.getElementById('live-timer').innerText = `${hrs}:${mins}:${secs}`;
        }

        function getAuthHeaders() {
          const token = localStorage.getItem('workout_token');
          return {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
          };
        }

        async function handleAuth(endpoint) {
          const username = document.getElementById('auth-user').value;
          const password = document.getElementById('auth-pass').value;
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
          });
          const data = await res.json();
          if (res.ok) {
            localStorage.setItem('workout_token', data.token);
            showApp(data.username);
          } else {
            alert(data.detail || 'Authentication failed');
          }
        }

        function logout() {
          localStorage.removeItem('workout_token');
          if (timerInterval) clearInterval(timerInterval);
          document.getElementById('app-screen').classList.add('hidden');
          document.getElementById('user-controls').classList.add('hidden');
          document.getElementById('auth-screen').classList.remove('hidden');
        }

        async function refreshExerciseOptions() {
          const res = await fetch('/api/exercises', { headers: getAuthHeaders() });
          if (res.ok) {
            exerciseCatalog = await res.json();
            document.getElementById('exercise-options').innerHTML = exerciseCatalog
              .map(e => `<option value="${e.name}">`)
              .join('');
          }
        }

        async function refreshSavedFoods() {
          const res = await fetch('/api/nutrition/foods', { headers: getAuthHeaders() });
          if (res.ok) {
            savedFoodsCatalog = await res.json();
            document.getElementById('food-catalog-options').innerHTML = savedFoodsCatalog
              .map(f => `<option value="${f.food_name}">`)
              .join('');
          }
        }

        function onFoodNameInput() {
          const enteredName = document.getElementById('food-name').value.trim().toLowerCase();
          const match = savedFoodsCatalog.find(f => f.food_name.toLowerCase() === enteredName);
          if (match) {
            if (match.unit) document.getElementById('food-unit').value = match.unit;
            document.getElementById('food-name').dataset.calRate = match.calories_per_unit;
            document.getElementById('food-name').dataset.proRate = match.protein_per_unit;
            document.getElementById('food-name').dataset.carbRate = match.carbs_per_unit;
            document.getElementById('food-name').dataset.fatRate = match.fats_per_unit;
            onFoodAmountInput();
          } else {
            delete document.getElementById('food-name').dataset.calRate;
          }
        }

        function onFoodAmountInput() {
          const amount = parseFloat(document.getElementById('food-amount').value);
          const nameInput = document.getElementById('food-name');
          
          if (isNaN(amount) || amount <= 0 || !nameInput.dataset.calRate) return;

          const calRate = parseFloat(nameInput.dataset.calRate);
          const proRate = parseFloat(nameInput.dataset.proRate);
          const carbRate = parseFloat(nameInput.dataset.carbRate);
          const fatRate = parseFloat(nameInput.dataset.fatRate);

          document.getElementById('food-cal').value = Math.round(calRate * amount);
          document.getElementById('food-pro').value = Math.round((proRate * amount) * 10) / 10;
          document.getElementById('food-carb').value = Math.round((carbRate * amount) * 10) / 10;
          document.getElementById('food-fat').value = Math.round((fatRate * amount) * 10) / 10;
        }

        // --- Grouped Exercise & Set Card Builders ---

        function renumberSets(exerciseCard) {
          const badges = exerciseCard.querySelectorAll('.set-num');
          badges.forEach((b, idx) => {
            b.innerText = idx + 1;
          });
        }

        function addSetToExercise(exerciseCard, prefill = {}) {
          const setsList = exerciseCard.querySelector('.exercise-sets-list');
          const setItem = document.createElement('div');
          setItem.className = 'set-item';

          const lbsVal = prefill.weight !== null && prefill.weight !== undefined ? prefill.weight : '';
          const repsVal = prefill.reps !== null && prefill.reps !== undefined ? prefill.reps : '';
          const rpeVal = prefill.rpe !== null && prefill.rpe !== undefined ? prefill.rpe : '';
          const noteVal = prefill.notes ? prefill.notes.replace(/"/g, '&quot;') : '';
          const nextNum = setsList.children.length + 1;

          setItem.innerHTML = `
            <div class="set-row">
              <span class="set-num">${nextNum}</span>
              <input type="number" class="set-lbs" placeholder="lbs" step="2.5" value="${lbsVal}">
              <input type="number" class="set-reps" placeholder="reps" value="${repsVal}">
              <input type="number" class="set-rpe" placeholder="RPE" step="0.5" min="1" max="10" value="${rpeVal}">
              <button type="button" class="btn-del" title="Remove set" onclick="removeSetFromExercise(this)">✕</button>
            </div>
            <input type="text" class="set-note" placeholder="Set note (optional)..." value="${noteVal}">
          `;
          setsList.appendChild(setItem);
        }

        function removeSetFromExercise(btn) {
          const exerciseCard = btn.closest('.exercise-card');
          btn.closest('.set-item').remove();
          if (exerciseCard.querySelectorAll('.set-item').length === 0) {
            exerciseCard.remove();
          } else {
            renumberSets(exerciseCard);
          }
        }

        function addExerciseBlock(exerciseName = '', setsPrefill = []) {
          const container = document.getElementById('exercises-container');
          const card = document.createElement('div');
          card.className = 'exercise-card';

          const exVal = exerciseName ? exerciseName.replace(/"/g, '&quot;') : '';

          card.innerHTML = `
            <div class="exercise-header">
              <input type="text" class="ex-name" list="exercise-options" placeholder="Exercise name (e.g. Bench Press)..." value="${exVal}">
              <button type="button" class="btn-del" title="Remove entire exercise" onclick="this.closest('.exercise-card').remove()">✕</button>
            </div>
            <div class="exercise-sets-list"></div>
            <button type="button" class="btn-add-set" onclick="addSetToExercise(this.closest('.exercise-card'))">+ Add Set</button>
          `;
          container.appendChild(card);

          if (setsPrefill.length > 0) {
            setsPrefill.forEach(s => addSetToExercise(card, s));
          } else {
            addSetToExercise(card);
          }
        }

        function collectFlattenedSets(requireReps = true) {
          const exerciseCards = document.querySelectorAll('.exercise-card');
          const flattened = [];
          let globalSetCounter = 1;

          exerciseCards.forEach(card => {
            const exName = card.querySelector('.ex-name').value.trim();
            if (!exName) return;

            const setItems = card.querySelectorAll('.set-item');
            setItems.forEach(item => {
              const lbsVal = item.querySelector('.set-lbs').value;
              const repsVal = item.querySelector('.set-reps').value;
              const rpeVal = item.querySelector('.set-rpe').value;
              const noteVal = item.querySelector('.set-note').value.trim() || null;

              const lbs = lbsVal !== '' ? parseFloat(lbsVal) : (requireReps ? 0 : null);
              const reps = repsVal !== '' ? parseInt(repsVal) : (requireReps ? 0 : null);
              const rpe = rpeVal !== '' ? parseFloat(rpeVal) : null;

              if (!requireReps || (reps && reps > 0)) {
                flattened.push({
                  exercise_name: exName,
                  set_number: globalSetCounter++,
                  weight: lbs,
                  reps: reps,
                  rpe: rpe,
                  notes: noteVal
                });
              }
            });
          });

          return flattened;
        }

        async function refreshTemplates() {
          const res = await fetch('/api/templates', { headers: getAuthHeaders() });
          if (res.ok) {
            savedTemplates = await res.json();
            const select = document.getElementById('template-select');
            select.innerHTML = '<option value="">-- Select a Workout Template --</option>' +
              savedTemplates.map(t => `<option value="${t.id}">${t.name} (${t.sets.length} sets)</option>`).join('');
          }
        }

        function loadSelectedTemplate() {
          const templateId = document.getElementById('template-select').value;
          if (!templateId) {
            alert('Choose a template from the dropdown first.');
            return;
          }
          const tpl = savedTemplates.find(t => t.id === templateId);
          if (!tpl) return;

          document.getElementById('workout-title').value = tpl.name;
          document.getElementById('workout-notes').value = tpl.notes || '';
          document.getElementById('exercises-container').innerHTML = '';

          if (tpl.sets.length === 0) {
            addExerciseBlock();
            return;
          }

          // Group consecutive sets sharing the same exercise name into one Exercise Card
          const groups = [];
          tpl.sets.forEach(s => {
            const last = groups[groups.length - 1];
            if (last && last.exercise_name.toLowerCase() === s.exercise_name.toLowerCase()) {
              last.sets.push(s);
            } else {
              groups.push({ exercise_name: s.exercise_name, sets: [s] });
            }
          });

          groups.forEach(g => addExerciseBlock(g.exercise_name, g.sets));
        }

        async function saveAsTemplate() {
          const defaultName = document.getElementById('workout-title').value.trim() || 'My Routine';
          const templateName = prompt('Name this workout template:', defaultName);
          if (!templateName || !templateName.trim()) return;

          const notes = document.getElementById('workout-notes').value.trim() || null;
          const sets = collectFlattenedSets(false);

          if (sets.length === 0) {
            alert('Enter at least one exercise name before saving a template.');
            return;
          }

          const res = await fetch('/api/templates', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
              name: templateName.trim(),
              notes,
              sets
            })
          });

          if (res.ok) {
            await refreshTemplates();
            await refreshExerciseOptions();
            alert(`Saved template "${templateName.trim()}"!`);
          } else {
            const err = await res.json();
            alert(err.detail || 'Failed to save template.');
          }
        }

        async function deleteSelectedTemplate() {
          const templateId = document.getElementById('template-select').value;
          if (!templateId) return;
          const tpl = savedTemplates.find(t => t.id === templateId);
          if (!confirm(`Delete template "${tpl ? tpl.name : 'selected'}"?`)) return;

          const res = await fetch(`/api/templates/${templateId}`, {
            method: 'DELETE',
            headers: getAuthHeaders()
          });

          if (res.ok) {
            await refreshTemplates();
          } else {
            alert('Failed to delete template.');
          }
        }

        async function showApp(username) {
          document.getElementById('auth-screen').classList.add('hidden');
          document.getElementById('app-screen').classList.remove('hidden');
          document.getElementById('user-controls').classList.remove('hidden');
          document.getElementById('user-badge').innerText = '@' + username;

          document.getElementById('start-time').value = toLocalDatetimeInput(new Date());
          document.getElementById('nutrition-date').value = toLocalDateString(new Date());
          switchTab('log');

          await Promise.all([refreshExerciseOptions(), refreshTemplates(), refreshSavedFoods()]);
          document.getElementById('exercises-container').innerHTML = '';
          addExerciseBlock();
          loadHistory();
        }

        async function init() {
          const token = localStorage.getItem('workout_token');
          if (!token) return;
          const meRes = await fetch('/api/me', { headers: getAuthHeaders() });
          if (meRes.ok) {
            const me = await meRes.json();
            showApp(me.username);
          } else {
            logout();
          }
        }

        async function submitWorkout() {
          const title = document.getElementById('workout-title').value;
          const notes = document.getElementById('workout-notes').value;
          const startVal = document.getElementById('start-time').value;
          const endVal = document.getElementById('end-time').value;

          const startedAt = startVal ? new Date(startVal).toISOString() : null;
          const endedAt = endVal ? new Date(endVal).toISOString() : new Date().toISOString();

          const sets = collectFlattenedSets(true);

          if (sets.length === 0) {
            alert('Please enter an exercise name and reps for at least one set.');
            return;
          }

          const res = await fetch('/api/workouts', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
              title,
              notes,
              started_at: startedAt,
              ended_at: endedAt,
              sets
            })
          });

          if (res.ok) {
            if (timerInterval) clearInterval(timerInterval);
            document.getElementById('live-timer').innerText = '00:00:00';
            const btn = document.getElementById('start-btn');
            btn.innerText = '▶ Start Now';
            btn.style.background = 'var(--green)';

            document.getElementById('exercises-container').innerHTML = '';
            document.getElementById('workout-notes').value = '';
            document.getElementById('start-time').value = toLocalDatetimeInput(new Date());
            document.getElementById('end-time').value = '';
            
            await refreshExerciseOptions();
            addExerciseBlock();
            loadHistory();
            alert('Workout saved!');
          } else {
            const err = await res.json();
            alert(err.detail || 'Failed to save workout');
          }
        }

        // --- Nutrition Functions ---

        async function loadNutrition() {
          const dateStr = document.getElementById('nutrition-date').value;
          if (!dateStr) return;

          const res = await fetch(`/api/nutrition?date=${dateStr}`, { headers: getAuthHeaders() });
          if (!res.ok) return;
          const data = await res.json();

          document.getElementById('tot-cal').innerText = data.totals.calories;
          document.getElementById('tot-pro').innerText = data.totals.protein + 'g';
          document.getElementById('tot-carb').innerText = data.totals.carbs + 'g';
          document.getElementById('tot-fat').innerText = data.totals.fats + 'g';

          const container = document.getElementById('nutrition-items-container');
          if (data.items.length === 0) {
            container.innerHTML = '<p style="color: var(--muted)">No food logged for this date.</p>';
            return;
          }

          container.innerHTML = data.items.map(i => {
            const amtStr = i.amount !== null && i.amount !== undefined ? `${i.amount} ${i.unit || ''}` : '';
            return `
              <div style="display:flex; justify-content:space-between; align-items:center; background:#252525; padding:8px 12px; border-radius:8px; margin-bottom:6px; border:1px solid var(--border);">
                <div>
                  <strong>${i.food_name}</strong> ${amtStr ? `<span style="font-size:13px; color:var(--muted);">(${amtStr.trim()})</span>` : ''}
                  <div style="font-size:12px; color:var(--muted);">🔥 ${i.calories} cals | P: ${i.protein}g | C: ${i.carbs}g | F: ${i.fats}g</div>
                </div>
                <button class="btn-del" onclick="deleteFoodItem('${i.id}')" style="padding:6px 10px;">✕</button>
              </div>
            `;
          }).join('');
        }

        async function loadNutritionHistory() {
          const container = document.getElementById('nutrition-history-container');
          container.innerHTML = '<p style="color: var(--muted)">Loading diet history...</p>';

          const res = await fetch('/api/nutrition/history', { headers: getAuthHeaders() });
          if (!res.ok) {
            container.innerHTML = '<p style="color: #dc2626;">Failed to load nutrition history.</p>';
            return;
          }

          const data = await res.json();
          if (data.length === 0) {
            container.innerHTML = '<p style="color: var(--muted)">No past nutrition logs found.</p>';
            return;
          }

          container.innerHTML = data.map(d => `
            <div class="history-item">
              <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
                <strong>📅 ${d.date}</strong>
                <span class="badge">🔥 ${d.totals.calories} cals | P: ${d.totals.protein}g | C: ${d.totals.carbs}g | F: ${d.totals.fats}g</span>
              </div>
              <ul style="margin: 6px 0 0 16px; padding:0; font-size:14px;">
                ${d.items.map(i => {
                  const amtStr = i.amount !== null && i.amount !== undefined ? `${i.amount} ${i.unit || ''}` : '';
                  return `
                    <li style="margin-bottom:4px;">
                      <strong>${i.food_name}</strong> ${amtStr ? `(${amtStr.trim()})` : ''}: 
                      🔥 ${i.calories} cals (P: ${i.protein}g, C: ${i.carbs}g, F: ${i.fats}g)
                    </li>
                  `;
                }).join('')}
              </ul>
            </div>
          `).join('');
        }

        async function addFoodItem() {
          const dateStr = document.getElementById('nutrition-date').value;
          const foodName = document.getElementById('food-name').value.trim();
          const amountVal = document.getElementById('food-amount').value;
          const unitVal = document.getElementById('food-unit').value.trim();
          const calories = parseInt(document.getElementById('food-cal').value);
          const protein = parseFloat(document.getElementById('food-pro').value) || 0;
          const carbs = parseFloat(document.getElementById('food-carb').value) || 0;
          const fats = parseFloat(document.getElementById('food-fat').value) || 0;

          if (!foodName || isNaN(calories)) {
            alert('Please enter a food name and calorie amount.');
            return;
          }

          const res = await fetch('/api/nutrition', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
              date: dateStr,
              food_name: foodName,
              amount: amountVal !== '' ? parseFloat(amountVal) : null,
              unit: unitVal || null,
              calories: calories,
              protein: protein,
              carbs: carbs,
              fats: fats
            })
          });

          if (res.ok) {
            document.getElementById('food-name').value = '';
            document.getElementById('food-amount').value = '';
            document.getElementById('food-unit').value = '';
            document.getElementById('food-cal').value = '';
            document.getElementById('food-pro').value = '';
            document.getElementById('food-carb').value = '';
            document.getElementById('food-fat').value = '';
            delete document.getElementById('food-name').dataset.calRate;
            
            await refreshSavedFoods();
            loadNutrition();
          } else {
            alert('Failed to log food item.');
          }
        }

        async function deleteFoodItem(id) {
          const res = await fetch(`/api/nutrition/${id}`, {
            method: 'DELETE',
            headers: getAuthHeaders()
          });
          if (res.ok) {
            loadNutrition();
          } else {
            alert('Failed to delete item.');
          }
        }

        function openTDEECalculator() {
          const weightLbs = prompt('Enter your body weight in lbs (e.g. 215):', '215');
          if (!weightLbs) return;
          const weightKg = parseFloat(weightLbs) / 2.20462;
          
          const bmr = (10 * weightKg) + (6.25 * 177) - (5 * 25) + 5;
          const tdee = Math.round(bmr * 1.55);

          const goal = prompt(`Estimated Maintenance (TDEE): ~${tdee} cals/day.\\n\\nSelect goal:\\n1. Bulk (+300 cals)\\n2. Cut (-500 cals)\\n3. Maintain`, '1');
          
          let target = tdee;
          if (goal === '1') target += 300;
          else if (goal === '2') target -= 500;

          alert(`Recommended Daily Calorie Target: ${target} kcal\\nRecommended Daily Protein Target: ~${Math.round(parseFloat(weightLbs))}g`);
        }

        // --- History & Exercise Functions ---

        function formatTimeRange(w) {
          if (!w.started_at) return w.date;
          const start = new Date(w.started_at);
          const end = w.ended_at ? new Date(w.ended_at) : null;
          const timeOpts = { hour: 'numeric', minute: '2-digit' };
          const startStr = start.toLocaleTimeString([], timeOpts);
          const endStr = end ? end.toLocaleTimeString([], timeOpts) : '';
          const durStr = w.duration_mins ? ` • ${w.duration_mins} min` : '';
          return `${w.date} (${startStr}${endStr ? ' – ' + endStr : ''}${durStr})`;
        }

        async function loadHistory() {
          const res = await fetch('/api/workouts', { headers: getAuthHeaders() });
          const data = await res.json();
          const container = document.getElementById('history-container');

          if (data.length === 0) {
            container.innerHTML = '<p style="color: var(--muted)">No workouts logged yet.</p>';
            return;
          }

          container.innerHTML = data.map(w => {
            // Group consecutive sets by exercise in history view
            const grouped = [];
            w.sets.forEach(s => {
              const last = grouped[grouped.length - 1];
              if (last && last.exercise.toLowerCase() === s.exercise.toLowerCase()) {
                last.sets.push(s);
              } else {
                grouped.push({ exercise: s.exercise, sets: [s] });
              }
            });

            return `
              <div class="history-item">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
                  <strong>${w.title}</strong>
                  <span class="badge">${formatTimeRange(w)}</span>
                </div>
                ${w.notes ? `<p style="font-size:13px; color:var(--muted); margin:4px 0;">${w.notes}</p>` : ''}
                <div style="margin-top:8px;">
                  ${grouped.map(g => `
                    <div style="margin-bottom:6px;">
                      <div style="font-weight:600; font-size:14px;">${g.exercise}</div>
                      <ul style="margin:2px 0 0 18px; padding:0; font-size:13px; color:#d1d5db;">
                        ${g.sets.map((s, idx) => `
                          <li style="margin-bottom:2px;">
                            Set ${idx + 1}: ${s.weight} lbs × ${s.reps} reps ${s.rpe ? `(@ RPE ${s.rpe})` : ''}
                            ${s.notes ? `<span style="color:var(--muted);"> — ${s.notes}</span>` : ''}
                          </li>
                        `).join('')}
                      </ul>
                    </div>
                  `).join('')}
                </div>
              </div>
            `;
          }).join('');
        }

        async function loadExerciseHistory() {
          const exerciseName = document.getElementById('exercise-search-input').value.trim();
          const container = document.getElementById('exercise-history-container');

          if (!exerciseName) {
            container.innerHTML = '<p style="color: var(--muted)">Please select or type an exercise name.</p>';
            return;
          }

          container.innerHTML = '<p style="color: var(--muted)">Loading performance history...</p>';

          const res = await fetch(`/api/exercises/history?name=${encodeURIComponent(exerciseName)}`, {
            headers: getAuthHeaders()
          });

          if (!res.ok) {
            container.innerHTML = '<p style="color: #dc2626;">Failed to load exercise history.</p>';
            return;
          }

          const history = await res.json();

          if (history.length === 0) {
            container.innerHTML = `<p style="color: var(--muted)">No recorded performances found for "${exerciseName}".</p>`;
            return;
          }

          container.innerHTML = history.map(h => `
            <div class="history-item">
              <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
                <strong>${h.workout_title}</strong>
                <span class="badge">${h.date}</span>
              </div>
              <ul style="margin: 6px 0 0 16px; padding:0; font-size:14px;">
                ${h.sets.map((s, idx) => `
                  <li style="margin-bottom:4px;">
                    Set ${idx + 1}: <strong>${s.weight} lbs</strong> × <strong>${s.reps} reps</strong> ${s.rpe ? `(@ RPE ${s.rpe})` : ''}
                    ${s.notes ? `<div style="font-size:12px; color:var(--muted); margin-top:2px;">↳ ${s.notes}</div>` : ''}
                  </li>
                `).join('')}
              </ul>
            </div>
          `).join('');
        }

        init();
      </script>
    </body>
    </html>
    """