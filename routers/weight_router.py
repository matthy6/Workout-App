from datetime import date, datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from auth import get_current_user
from database import get_session
from models import BodyWeightCreate, BodyWeightLog, FoodLog, SetLog, User, WorkoutSession

router = APIRouter(prefix="/api/weight", tags=["weight"])


def _parse_reference_date(ref_date: Optional[str]) -> date:
    if ref_date:
        try:
            return date.fromisoformat(ref_date.strip())
        except ValueError:
            pass
    return datetime.now().astimezone().date()


def _cutoff_date_str(range_key: str, ref_date: Optional[str] = None) -> Optional[str]:
    anchor = _parse_reference_date(ref_date)
    days_map = {
        "1d": 1,
        "1w": 7,
        "1m": 30,
        "3m": 90,
        "6m": 180,
        "1y": 365,
    }
    key = range_key.lower()
    if key == "all":
        return None
    if key not in days_map:
        raise HTTPException(
            status_code=400,
            detail="Invalid range. Use 1d, 1w, 1m, 3m, 6m, 1y, or all.",
        )
    return (anchor - timedelta(days=days_map[key])).isoformat()


@router.post("", status_code=status.HTTP_201_CREATED)
def upsert_weight_log(
    payload: BodyWeightCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if payload.weight <= 0:
        raise HTTPException(status_code=400, detail="Weight must be greater than 0.")
    clean_date = payload.date.strip()
    if not clean_date:
        raise HTTPException(status_code=400, detail="Date is required.")

    unit = (payload.unit or "lbs").strip().lower()
    if unit not in ("lbs", "kg"):
        unit = "lbs"

    clean_notes = payload.notes.strip() if payload.notes else None
    rounded_weight = round(float(payload.weight), 2)

    existing = session.exec(
        select(BodyWeightLog).where(
            BodyWeightLog.user_id == current_user.id,
            BodyWeightLog.date == clean_date,
        )
    ).first()

    if existing:
        existing.weight = rounded_weight
        existing.unit = unit
        existing.notes = clean_notes
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return {
            "status": "updated",
            "id": str(existing.id),
            "date": existing.date,
            "weight": existing.weight,
            "unit": existing.unit,
        }

    entry = BodyWeightLog(
        user_id=current_user.id,
        date=clean_date,
        weight=rounded_weight,
        unit=unit,
        notes=clean_notes,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return {
        "status": "created",
        "id": str(entry.id),
        "date": entry.date,
        "weight": entry.weight,
        "unit": entry.unit,
    }


@router.get("")
def get_weight_series(
    range: str = Query(default="1m"),
    ref_date: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_date_str(range, ref_date=ref_date)
    stmt = (
        select(BodyWeightLog)
        .where(BodyWeightLog.user_id == current_user.id)
        .order_by(BodyWeightLog.date.asc())
    )
    if cutoff:
        stmt = stmt.where(BodyWeightLog.date >= cutoff)

    entries = session.exec(stmt).all()
    return [
        {
            "id": str(e.id),
            "date": e.date,
            "weight": e.weight,
            "unit": e.unit,
            "notes": e.notes,
        }
        for e in entries
    ]


@router.get("/day")
def get_day_snapshot(
    date: str = Query(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    weight_entry = session.exec(
        select(BodyWeightLog).where(
            BodyWeightLog.user_id == current_user.id,
            BodyWeightLog.date == date,
        )
    ).first()

    food_logs = session.exec(
        select(FoodLog).where(
            FoodLog.user_id == current_user.id,
            FoodLog.date == date,
        )
    ).all()

    nutrition_totals = {
        "calories": sum(f.calories for f in food_logs),
        "protein": round(sum(f.protein or 0 for f in food_logs), 1),
        "carbs": round(sum(f.carbs or 0 for f in food_logs), 1),
        "fats": round(sum(f.fats or 0 for f in food_logs), 1),
    }

    workouts = session.exec(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == current_user.id)
        .order_by(WorkoutSession.date.desc())
    ).all()

    day_workouts = []
    for w in workouts:
        w_date_str = w.date.date().isoformat() if hasattr(w.date, "date") else str(w.date)[:10]
        if w_date_str == date:
            sets = session.exec(
                select(SetLog)
                .where(SetLog.workout_id == w.id)
                .order_by(SetLog.set_number.asc())
            ).all()
            day_workouts.append({
                "id": str(w.id),
                "title": w.title,
                "notes": w.notes,
                "sets": [
                    {
                        "exercise": s.exercise_name,
                        "set_number": s.set_number,
                        "weight": s.weight,
                        "weight_unit": s.weight_unit,
                        "reps": s.reps,
                        "rpe": s.rpe,
                        "notes": s.notes,
                    }
                    for s in sets
                ],
            })

    return {
        "date": date,
        "weight_entry": (
            {
                "id": str(weight_entry.id),
                "weight": weight_entry.weight,
                "unit": weight_entry.unit,
                "notes": weight_entry.notes,
            }
            if weight_entry
            else None
        ),
        "nutrition": {
            "totals": nutrition_totals,
            "items": [
                {
                    "id": str(f.id),
                    "food_name": f.food_name,
                    "amount": f.amount,
                    "unit": f.unit,
                    "calories": f.calories,
                    "protein": f.protein,
                    "carbs": f.carbs,
                    "fats": f.fats,
                }
                for f in food_logs
            ],
        },
        "workouts": day_workouts,
    }


@router.delete("/{entry_id}")
def delete_weight_log(
    entry_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    entry = session.exec(
        select(BodyWeightLog).where(
            BodyWeightLog.id == entry_id,
            BodyWeightLog.user_id == current_user.id,
        )
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Weight entry not found.")
    session.delete(entry)
    session.commit()
    return {"status": "deleted"}