import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select

load_dotenv()

# --- 1. Database Configuration ---

DATABASE_URL = (
    os.getenv("DATABASE_URL_POOLED")
    or os.getenv("DATABASE_URL")
    or "sqlite:///./workout.db"
)

# Fix for Render/Neon URLs starting with postgres:// instead of postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

# pool_pre_ping=True prevents dropped connection errors when Neon wakes up from sleep
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    workouts: List["WorkoutSession"] = Relationship(back_populates="user")


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

    workout: Optional[WorkoutSession] = Relationship(back_populates="sets")


# --- 4. Authentication Dependency ---

security = HTTPBasic()


def get_current_user(
    credentials: HTTPBasicCredentials = Depends(security),
    session: Session = Depends(get_session),
) -> User:
    user = session.exec(
        select(User).where(User.username == credentials.username.lower().strip())
    ).first()

    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


# --- 5. App Lifecycle & Initial Data Seed ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # Seed default exercises if table is empty
        existing = session.exec(select(Exercise)).first()
        if not existing:
            default_exercises = [
                Exercise(
                    name="Barbell Squat",
                    primary_muscle="Quads",
                    equipment="Barbell",
                ),
                Exercise(
                    name="Deadlift",
                    primary_muscle="Hamstrings/Back",
                    equipment="Barbell",
                ),
                Exercise(
                    name="Incline Bench Press",
                    primary_muscle="Upper Chest",
                    equipment="Barbell",
                ),
                Exercise(
                    name="Romanian Deadlift (RDL)",
                    primary_muscle="Hamstrings",
                    equipment="Barbell",
                ),
                Exercise(
                    name="Dumbbell Lateral Raise",
                    primary_muscle="Shoulders",
                    equipment="Dumbbell",
                ),
                Exercise(
                    name="Lat Pulldown",
                    primary_muscle="Back",
                    equipment="Cable",
                ),
                Exercise(
                    name="Triceps Rope Pushdown",
                    primary_muscle="Triceps",
                    equipment="Cable",
                ),
            ]
            session.add_all(default_exercises)
            session.commit()

        # Seed initial admin user from .env / Render Environment Variables if no users exist
        if not session.exec(select(User)).first():
            init_user = os.getenv("APP_USERNAME", "admin").lower().strip()
            init_pass = os.getenv("APP_PASSWORD", "changeme123")
            session.add(
                User(username=init_user, hashed_password=hash_password(init_pass))
            )
            session.commit()
    yield


app = FastAPI(title="Workout Tracker", lifespan=lifespan)

# --- 6. API Endpoints ---


class UserRegister(SQLModel):
    username: str
    password: str


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: UserRegister, session: Session = Depends(get_session)):
    clean_username = payload.username.lower().strip()
    if len(clean_username) < 3 or len(payload.password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Username must be at least 3 chars and password at least 6 chars.",
        )

    existing = session.exec(select(User).where(User.username == clean_username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken.")

    new_user = User(
        username=clean_username,
        hashed_password=hash_password(payload.password),
    )
    session.add(new_user)
    session.commit()
    return {"message": f"Account '{clean_username}' created!"}


@app.get("/api/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"id": str(current_user.id), "username": current_user.username}


@app.get("/api/exercises", response_model=List[Exercise])
def get_exercises(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return session.exec(select(Exercise)).all()


class SetInput(SQLModel):
    exercise_name: str
    set_number: int
    weight: float
    reps: int
    rpe: Optional[float] = None


class WorkoutCreate(SQLModel):
    title: str
    notes: Optional[str] = None
    sets: List[SetInput]


@app.post("/api/workouts", response_model=WorkoutSession)
def save_workout(
    payload: WorkoutCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workout = WorkoutSession(
        user_id=current_user.id,
        title=payload.title,
        notes=payload.notes,
    )
    session.add(workout)
    session.commit()
    session.refresh(workout)

    for item in payload.sets:
        set_entry = SetLog(
            workout_id=workout.id,
            exercise_name=item.exercise_name,
            set_number=item.set_number,
            weight=item.weight,
            reps=item.reps,
            rpe=item.rpe,
        )
        session.add(set_entry)

    session.commit()
    session.refresh(workout)
    return workout


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
        results.append(
            {
                "id": str(w.id),
                "title": w.title,
                "date": w.date.strftime("%Y-%m-%d %H:%M"),
                "notes": w.notes,
                "sets": [
                    {
                        "exercise": s.exercise_name,
                        "set": s.set_number,
                        "weight": s.weight,
                        "reps": s.reps,
                        "rpe": s.rpe,
                    }
                    for s in w.sets
                ],
            }
        )
    return results


# --- 7. Front-End Web Interface ---


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Workout Tracker</title>
      <style>
        :root { --bg: #121212; --card: #1e1e1e; --accent: #2563eb; --text: #f3f4f6; --muted: #9ca3af; --border: #333; }
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); margin: 0; padding: 16px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 600px; display: flex; flex-direction: column; gap: 16px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
        .header-bar { display: flex; justify-content: space-between; align-items: center; }
        h1, h2, h3 { margin: 0 0 12px 0; }
        input, select, textarea, button { width: 100%; padding: 10px; margin-bottom: 8px; border-radius: 6px; border: 1px solid var(--border); background: #2a2a2a; color: var(--text); font-size: 15px; }
        button { background: var(--accent); color: white; border: none; font-weight: 600; cursor: pointer; }
        button:hover { opacity: 0.9; }
        .set-row { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: 6px; align-items: center; margin-bottom: 8px; }
        .set-row input, .set-row select { margin: 0; }
        .btn-del { background: #dc2626; padding: 8px 12px; width: auto; }
        .btn-small { width: auto; padding: 6px 12px; font-size: 13px; background: #374151; }
        .history-item { border-bottom: 1px solid var(--border); padding: 12px 0; }
        .history-item:last-child { border: none; }
        .badge { font-size: 12px; background: #333; padding: 2px 6px; border-radius: 4px; color: var(--muted); }
        .hidden { display: none; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header-bar">
          <h1>🏋️ Workout Logger</h1>
          <div>
            <span id="user-badge" class="badge" style="margin-right:6px;"></span>
            <button class="btn-small" onclick="toggleRegister()">+ New Account</button>
          </div>
        </div>

        <!-- Account Creation Card (Hidden by default) -->
        <div id="register-card" class="card hidden">
          <h2>Create New Account</h2>
          <input type="text" id="reg-user" placeholder="New username (min 3 chars)">
          <input type="password" id="reg-pass" placeholder="New password (min 6 chars)">
          <button onclick="registerAccount()">Create Account</button>
        </div>

        <!-- Workout Logger Card -->
        <div class="card">
          <h2>Log Session</h2>
          <input type="text" id="workout-title" placeholder="Workout Title (e.g. Leg Day, Push Day)" value="Workout Session">
          <textarea id="workout-notes" placeholder="Session notes (optional)..." rows="2"></textarea>

          <h3>Sets</h3>
          <div id="sets-container"></div>
          
          <button type="button" onclick="addSetRow()" style="background: #374151; margin-bottom: 12px;">+ Add Set</button>
          <button type="button" onclick="submitWorkout()">Save Workout</button>
        </div>

        <!-- History Card -->
        <div class="card">
          <h2>Past Workouts</h2>
          <div id="history-container">Loading past workouts...</div>
        </div>
      </div>

      <script>
        let exerciseCatalog = [];

        function toggleRegister() {
          document.getElementById('register-card').classList.toggle('hidden');
        }

        async function registerAccount() {
          const username = document.getElementById('reg-user').value;
          const password = document.getElementById('reg-pass').value;
          const res = await fetch('/api/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
          });
          const data = await res.json();
          if (res.ok) {
            alert(data.message + " Refresh the page and sign in with your new credentials.");
            toggleRegister();
          } else {
            alert(data.detail || "Registration failed");
          }
        }

        async function init() {
          const meRes = await fetch('/api/me');
          if (meRes.ok) {
            const me = await meRes.json();
            document.getElementById('user-badge').innerText = '@' + me.username;
          }
          const res = await fetch('/api/exercises');
          exerciseCatalog = await res.json();
          addSetRow();
          loadHistory();
        }

        function addSetRow() {
          const container = document.getElementById('sets-container');
          const row = document.createElement('div');
          row.className = 'set-row';

          const exerciseSelect = exerciseCatalog.map(e => `<option value="${e.name}">${e.name}</option>`).join('');

          row.innerHTML = `
            <select class="set-ex">${exerciseSelect}</select>
            <input type="number" class="set-lbs" placeholder="lbs" step="2.5">
            <input type="number" class="set-reps" placeholder="reps">
            <input type="number" class="set-rpe" placeholder="RPE" step="0.5" min="1" max="10">
            <button class="btn-del" onclick="this.parentElement.remove()">✕</button>
          `;
          container.appendChild(row);
        }

        async function submitWorkout() {
          const title = document.getElementById('workout-title').value;
          const notes = document.getElementById('workout-notes').value;
          const rows = document.querySelectorAll('.set-row');

          const sets = [];
          rows.forEach((r, idx) => {
            const ex = r.querySelector('.set-ex').value;
            const lbs = parseFloat(r.querySelector('.set-lbs').value) || 0;
            const reps = parseInt(r.querySelector('.set-reps').value) || 0;
            const rpe = parseFloat(r.querySelector('.set-rpe').value) || null;
            if (reps > 0) {
              sets.push({ exercise_name: ex, set_number: idx + 1, weight: lbs, reps: reps, rpe: rpe });
            }
          });

          if (sets.length === 0) {
            alert('Please add at least one set with reps.');
            return;
          }

          const res = await fetch('/api/workouts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, notes, sets })
          });

          if (res.ok) {
            document.getElementById('sets-container').innerHTML = '';
            document.getElementById('workout-notes').value = '';
            addSetRow();
            loadHistory();
          } else {
            alert('Failed to save workout');
          }
        }

        async function loadHistory() {
          const res = await fetch('/api/workouts');
          const data = await res.json();
          const container = document.getElementById('history-container');

          if (data.length === 0) {
            container.innerHTML = '<p style="color: var(--muted)">No workouts logged yet.</p>';
            return;
          }

          container.innerHTML = data.map(w => `
            <div class="history-item">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong>${w.title}</strong>
                <span class="badge">${w.date}</span>
              </div>
              ${w.notes ? `<p style="font-size:13px; color:var(--muted); margin:4px 0;">${w.notes}</p>` : ''}
              <ul style="margin: 6px 0 0 16px; padding:0; font-size:14px;">
                ${w.sets.map(s => `<li>${s.exercise}: ${s.weight} lbs × ${s.reps} reps ${s.rpe ? `(@ RPE ${s.rpe})` : ''}</li>`).join('')}
              </ul>
            </div>
          `).join('');
        }

        init();
      </script>
    </body>
    </html>
    """