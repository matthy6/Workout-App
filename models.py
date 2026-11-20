from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, Relationship, SQLModel


# --- Database Tables ---


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    auth_token: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    split_template_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    split_index: int = Field(default=0)
    target_calories: Optional[int] = None
    target_protein: Optional[float] = None
    target_carbs: Optional[float] = None
    target_fats: Optional[float] = None

    workouts: List["WorkoutSession"] = Relationship(back_populates="user")
    templates: List["WorkoutTemplate"] = Relationship(back_populates="user")
    food_logs: List["FoodLog"] = Relationship(back_populates="user")
    saved_foods: List["FoodDatabase"] = Relationship(back_populates="user")
    body_weights: List["BodyWeightLog"] = Relationship(back_populates="user")


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
    weight: str
    weight_unit: Optional[str] = Field(default="lbs")
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
    weight: str
    weight_unit: Optional[str] = Field(default="lbs")
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


class BodyWeightLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    date: str = Field(index=True)  # YYYY-MM-DD format
    weight: float
    unit: str = Field(default="lbs")
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    user: Optional[User] = Relationship(back_populates="body_weights")


# --- Request / Payload Schemas ---


class AuthPayload(SQLModel):
    username: str
    password: str


class SplitUpdatePayload(SQLModel):
    template_ids: List[str]
    current_index: Optional[int] = 0


class MacroTargetsPayload(SQLModel):
    target_calories: Optional[int] = None
    target_protein: Optional[float] = None
    target_carbs: Optional[float] = None
    target_fats: Optional[float] = None


class FoodBankUpsert(SQLModel):
    food_name: str
    reference_amount: float = 1.0
    unit: Optional[str] = None
    calories: float
    protein: Optional[float] = 0.0
    carbs: Optional[float] = 0.0
    fats: Optional[float] = 0.0


class FoodLogCreate(SQLModel):
    date: str
    food_name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    calories: int
    protein: Optional[float] = 0.0
    carbs: Optional[float] = 0.0
    fats: Optional[float] = 0.0


class BodyWeightCreate(SQLModel):
    date: str
    weight: float
    unit: Optional[str] = "lbs"
    notes: Optional[str] = None


class TemplateSetInput(SQLModel):
    exercise_name: str
    set_number: int
    weight: str
    weight_unit: Optional[str] = "lbs"
    reps: Optional[int] = None
    rpe: Optional[float] = None
    notes: Optional[str] = None


class TemplateCreate(SQLModel):
    name: str
    notes: Optional[str] = None
    sets: List[TemplateSetInput]


class SetInput(SQLModel):
    exercise_name: str
    set_number: int
    weight: str
    weight_unit: Optional[str] = "lbs"
    reps: int
    rpe: Optional[float] = None
    notes: Optional[str] = None


class WorkoutCreate(SQLModel):
    title: str
    notes: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    sets: List[SetInput]