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


app = FastAPI(title="Workout Tracker", lifespan=lifespan)

# --- 6. Auth, Exercise, Template & Workout API Endpoints ---


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


# --- 7. Web Interface with Templates, Per-Set Notes, Custom Exercises & Timer ---


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
        .template-bar { display: grid; grid-template-columns: 1fr auto auto; gap: 6px; align-items: center; background: #262626; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border); }
        .template-bar select { margin: 0; font-size: 14px; padding: 8px; }
        .time-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
        .timer-bar { display: flex; justify-content: space-between; align-items: center; background: #262626; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border); }
        .set-card { background: #252525; border: 1px solid var(--border); border-radius: 8px; padding: 8px; margin-bottom: 10px; }
        .set-row { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: 6px; align-items: center; margin-bottom: 6px; }
        .set-row input, .set-row select { margin: 0; }
        .set-note { margin: 0; padding: 7px 10px; font-size: 13px; background: #1e1e1e; }
        .btn-del { background: #dc2626; padding: 8px 12px; width: auto; margin: 0; }
        .btn-small { width: auto; padding: 8px 12px; font-size: 13px; background: #374151; margin: 0; white-space: nowrap; }
        .action-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
        .action-row button { margin: 0; }
        .history-item { border-bottom: 1px solid var(--border); padding: 12px 0; }
        .history-item:last-child { border: none; }
        .badge { font-size: 12px; background: #333; padding: 4px 8px; border-radius: 4px; color: var(--muted); }
        .hidden { display: none !important; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header-bar">
          <h1>🏋️ Workout Logger</h1>
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
          <div class="card">
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

            <h3>Sets</h3>
            <datalist id="exercise-options"></datalist>
            <div id="sets-container"></div>
            
            <div class="action-row">
              <button type="button" onclick="addSetRow()" style="background: #374151;">+ Add Set</button>
              <button type="button" onclick="saveAsTemplate()" style="background: #4b5563;">📋 Save as Template</button>
            </div>
            <button type="button" onclick="submitWorkout()">Finish & Save Workout</button>
          </div>

          <div class="card">
            <h2>Past Workouts</h2>
            <div id="history-container">Loading past workouts...</div>
          </div>
        </div>
      </div>

      <script>
        let exerciseCatalog = [];
        let savedTemplates = [];
        let timerInterval = null;

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
          document.getElementById('sets-container').innerHTML = '';

          if (tpl.sets.length === 0) {
            addSetRow();
          } else {
            tpl.sets.forEach(s => addSetRow(s));
          }
        }

        async function saveAsTemplate() {
          const defaultName = document.getElementById('workout-title').value.trim() || 'My Routine';
          const templateName = prompt('Name this workout template:', defaultName);
          if (!templateName || !templateName.trim()) return;

          const notes = document.getElementById('workout-notes').value.trim() || null;
          const cards = document.querySelectorAll('.set-card');
          const sets = [];

          cards.forEach((c, idx) => {
            const ex = c.querySelector('.set-ex').value.trim();
            const lbsVal = c.querySelector('.set-lbs').value;
            const repsVal = c.querySelector('.set-reps').value;
            const rpeVal = c.querySelector('.set-rpe').value;
            const setNote = c.querySelector('.set-note').value.trim() || null;

            if (ex) {
              sets.push({
                exercise_name: ex,
                set_number: idx + 1,
                weight: lbsVal !== '' ? parseFloat(lbsVal) : null,
                reps: repsVal !== '' ? parseInt(repsVal) : null,
                rpe: rpeVal !== '' ? parseFloat(rpeVal) : null,
                notes: setNote
              });
            }
          });

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

          await Promise.all([refreshExerciseOptions(), refreshTemplates()]);
          document.getElementById('sets-container').innerHTML = '';
          addSetRow();
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

        function addSetRow(prefill = {}) {
          const container = document.getElementById('sets-container');
          const card = document.createElement('div');
          card.className = 'set-card';

          const exVal = prefill.exercise_name ? prefill.exercise_name.replace(/"/g, '&quot;') : '';
          const lbsVal = prefill.weight !== null && prefill.weight !== undefined ? prefill.weight : '';
          const repsVal = prefill.reps !== null && prefill.reps !== undefined ? prefill.reps : '';
          const rpeVal = prefill.rpe !== null && prefill.rpe !== undefined ? prefill.rpe : '';
          const noteVal = prefill.notes ? prefill.notes.replace(/"/g, '&quot;') : '';

          card.innerHTML = `
            <div class="set-row">
              <input type="text" class="set-ex" list="exercise-options" placeholder="Exercise name..." value="${exVal}">
              <input type="number" class="set-lbs" placeholder="lbs" step="2.5" value="${lbsVal}">
              <input type="number" class="set-reps" placeholder="reps" value="${repsVal}">
              <input type="number" class="set-rpe" placeholder="RPE" step="0.5" min="1" max="10" value="${rpeVal}">
              <button class="btn-del" onclick="this.closest('.set-card').remove()">✕</button>
            </div>
            <input type="text" class="set-note" placeholder="Set note (e.g., paused reps, beltless, form cue...)" value="${noteVal}">
          `;
          container.appendChild(card);
        }

        async function submitWorkout() {
          const title = document.getElementById('workout-title').value;
          const notes = document.getElementById('workout-notes').value;
          const startVal = document.getElementById('start-time').value;
          const endVal = document.getElementById('end-time').value;

          const startedAt = startVal ? new Date(startVal).toISOString() : null;
          const endedAt = endVal ? new Date(endVal).toISOString() : new Date().toISOString();

          const cards = document.querySelectorAll('.set-card');
          const sets = [];

          cards.forEach((c, idx) => {
            const ex = c.querySelector('.set-ex').value.trim();
            const lbs = parseFloat(c.querySelector('.set-lbs').value) || 0;
            const reps = parseInt(c.querySelector('.set-reps').value) || 0;
            const rpe = parseFloat(c.querySelector('.set-rpe').value) || null;
            const setNote = c.querySelector('.set-note').value.trim() || null;
            if (ex && reps > 0) {
              sets.push({
                exercise_name: ex,
                set_number: idx + 1,
                weight: lbs,
                reps: reps,
                rpe: rpe,
                notes: setNote
              });
            }
          });

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

            document.getElementById('sets-container').innerHTML = '';
            document.getElementById('workout-notes').value = '';
            document.getElementById('start-time').value = toLocalDatetimeInput(new Date());
            document.getElementById('end-time').value = '';
            
            await refreshExerciseOptions();
            addSetRow();
            loadHistory();
          } else {
            const err = await res.json();
            alert(err.detail || 'Failed to save workout');
          }
        }

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

          container.innerHTML = data.map(w => `
            <div class="history-item">
              <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
                <strong>${w.title}</strong>
                <span class="badge">${formatTimeRange(w)}</span>
              </div>
              ${w.notes ? `<p style="font-size:13px; color:var(--muted); margin:4px 0;">${w.notes}</p>` : ''}
              <ul style="margin: 6px 0 0 16px; padding:0; font-size:14px;">
                ${w.sets.map(s => `
                  <li style="margin-bottom:4px;">
                    ${s.exercise}: ${s.weight} lbs × ${s.reps} reps ${s.rpe ? `(@ RPE ${s.rpe})` : ''}
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