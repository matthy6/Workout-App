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

    workout: Optional[WorkoutSession] = Relationship(back_populates="sets")


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

# --- 6. Auth & Workout API Endpoints ---


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
        session.add(
            SetLog(
                workout_id=workout.id,
                exercise_name=item.exercise_name,
                set_number=item.set_number,
                weight=item.weight,
                reps=item.reps,
                rpe=item.rpe,
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
                    }
                    for s in w.sets
                ],
            }
        )
    return results


# --- 7. Web Interface with Start/End Time & Live Timer ---


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
        .time-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
        .timer-bar { display: flex; justify-content: space-between; align-items: center; background: #262626; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border); }
        .set-row { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: 6px; align-items: center; margin-bottom: 8px; }
        .set-row input, .set-row select { margin: 0; }
        .btn-del { background: #dc2626; padding: 8px 12px; width: auto; }
        .btn-small { width: auto; padding: 6px 12px; font-size: 13px; background: #374151; margin: 0; }
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

            <textarea id="workout-notes" placeholder="Session notes (optional)..." rows="2"></textarea>

            <h3>Sets</h3>
            <div id="sets-container"></div>
            
            <button type="button" onclick="addSetRow()" style="background: #374151; margin-bottom: 12px;">+ Add Set</button>
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

        async function showApp(username) {
          document.getElementById('auth-screen').classList.add('hidden');
          document.getElementById('app-screen').classList.remove('hidden');
          document.getElementById('user-controls').classList.remove('hidden');
          document.getElementById('user-badge').innerText = '@' + username;

          // Default Start Time to when the user opens the logger
          document.getElementById('start-time').value = toLocalDatetimeInput(new Date());

          const res = await fetch('/api/exercises', { headers: getAuthHeaders() });
          exerciseCatalog = await res.json();
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
          const startVal = document.getElementById('start-time').value;
          const endVal = document.getElementById('end-time').value;

          const startedAt = startVal ? new Date(startVal).toISOString() : null;
          const endedAt = endVal ? new Date(endVal).toISOString() : new Date().toISOString();

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