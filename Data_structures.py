from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field


app = FastAPI(title="Workout Tracker API")


# ==========================================
# 1. Schemas (Data Models)
# ==========================================

class SetType(str, Enum):
    WARMUP = "warmup"
    WORKING = "working"
    DROPSET = "dropset"
    FAILURE = "failure"


class SetLog(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    set_number: int
    set_type: SetType = SetType.WORKING
    weight: Optional[float] = None
    reps: Optional[int] = None
    duration_seconds: Optional[int] = None
    rpe: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    rir: Optional[int] = Field(default=None, ge=0, le=10)
    notes: Optional[str] = None


class WorkoutExercise(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    exercise_id: UUID
    order_index: int
    sets: List[SetLog] = []


# Payload sent by the mobile app/client to CREATE a workout
class WorkoutSessionCreate(BaseModel):
    user_id: UUID
    title: str
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    notes: Optional[str] = None
    exercises: List[WorkoutExercise] = []


# Full workout representation returned by the API (includes permanent server ID)
class WorkoutSession(WorkoutSessionCreate):
    id: UUID = Field(default_factory=uuid4)


# Payload for updating high-level details of a workout
class WorkoutSessionUpdate(BaseModel):
    title: Optional[str] = None
    end_time: Optional[datetime] = None
    notes: Optional[str] = None


# ==========================================
# 2. In-Memory Database (Mock Storage)
# ==========================================

# Acts as our temporary table: { workout_id: WorkoutSession }
db_workouts: dict[UUID, WorkoutSession] = {}


# ==========================================
# 3. CRUD Routes for Workouts
# ==========================================

# CREATE: Start or log a new workout
@app.post(
    "/workouts",
    response_model=WorkoutSession,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workout session",
)
def create_workout(payload: WorkoutSessionCreate):
    # Convert input payload into a full WorkoutSession with its own unique ID
    new_workout = WorkoutSession(**payload.model_dump())
    db_workouts[new_workout.id] = new_workout
    return new_workout


# READ ALL / FILTER: Get all workouts (optionally by user_id)
@app.get(
    "/workouts",
    response_model=List[WorkoutSession],
    summary="List all workout sessions",
)
def list_workouts(user_id: Optional[UUID] = None):
    if user_id:
        return [w for w in db_workouts.values() if w.user_id == user_id]
    return list(db_workouts.values())


# READ ONE: Fetch a single workout by its ID
@app.get(
    "/workouts/{workout_id}",
    response_model=WorkoutSession,
    summary="Get a specific workout session by ID",
)
def get_workout(workout_id: UUID):
    if workout_id not in db_workouts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workout with ID '{workout_id}' not found",
        )
    return db_workouts[workout_id]


# UPDATE: Modify workout metadata (e.g., mark finish time, update notes)
@app.patch(
    "/workouts/{workout_id}",
    response_model=WorkoutSession,
    summary="Update workout session details",
)
def update_workout(workout_id: UUID, payload: WorkoutSessionUpdate):
    if workout_id not in db_workouts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workout with ID '{workout_id}' not found",
        )

    stored_workout = db_workouts[workout_id]
    # Only update fields that the caller actually provided (exclude_unset=True)
    update_data = payload.model_dump(exclude_unset=True)
    updated_workout = stored_workout.model_copy(update=update_data)
    
    db_workouts[workout_id] = updated_workout
    return updated_workout


# DELETE: Remove a workout session
@app.delete(
    "/workouts/{workout_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workout session",
)
def delete_workout(workout_id: UUID):
    if workout_id not in db_workouts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workout with ID '{workout_id}' not found",
        )
    del db_workouts[workout_id]
    return None