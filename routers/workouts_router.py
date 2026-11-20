from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from auth import get_current_user
from database import get_session
from models import (
    Exercise,
    SetLog,
    SplitUpdatePayload,
    TemplateCreate,
    TemplateSet,
    User,
    WorkoutCreate,
    WorkoutSession,
    WorkoutTemplate,
)

router = APIRouter(prefix="/api", tags=["workouts"])


# --- Split Helpers & Endpoints ---


def resolve_user_split(user: User, session: Session) -> dict:
    raw_ids = user.split_template_ids or []
    user_templates = session.exec(
        select(WorkoutTemplate).where(WorkoutTemplate.user_id == user.id)
    ).all()
    valid_map = {str(t.id): t for t in user_templates}

    clean_ids = [tid for tid in raw_ids if tid in valid_map]
    idx = user.split_index or 0
    if clean_ids:
        idx = idx % len(clean_ids)
    else:
        idx = 0

    if clean_ids != raw_ids or idx != (user.split_index or 0):
        user.split_template_ids = clean_ids
        user.split_index = idx
        session.add(user)
        session.commit()
        session.refresh(user)

    next_template_id = clean_ids[idx] if clean_ids else None
    return {
        "template_ids": clean_ids,
        "current_index": idx,
        "next_template_id": next_template_id,
    }


@router.get("/split")
def get_workout_split(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return resolve_user_split(current_user, session)


@router.put("/split")
def update_workout_split(
    payload: SplitUpdatePayload,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user_templates = session.exec(
        select(WorkoutTemplate).where(WorkoutTemplate.user_id == current_user.id)
    ).all()
    valid_ids = {str(t.id) for t in user_templates}

    clean_ids = [tid for tid in payload.template_ids if tid in valid_ids]
    idx = payload.current_index or 0
    if clean_ids:
        idx = idx % len(clean_ids)
    else:
        idx = 0

    current_user.split_template_ids = clean_ids
    current_user.split_index = idx
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return resolve_user_split(current_user, session)


# --- Exercise Endpoints ---


@router.get("/exercises")
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


@router.get("/exercises/history")
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
                    "sets": [],
                }
            history_map[workout.id]["sets"].append({
                "set_number": set_log.set_number,
                "weight": set_log.weight,
                "weight_unit": set_log.weight_unit or "lbs",
                "reps": set_log.reps,
                "rpe": set_log.rpe,
                "notes": set_log.notes,
            })

    results = []
    for w_data in history_map.values():
        w_data["sets"] = sorted(w_data["sets"], key=lambda x: x["set_number"])
        results.append(w_data)

    return results


# --- Template Endpoints ---


@router.post("/templates", status_code=status.HTTP_201_CREATED)
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
                weight=str(item.weight),
                weight_unit=item.weight_unit or "lbs",
                reps=item.reps,
                rpe=item.rpe,
                notes=item.notes.strip() if item.notes else None,
            )
        )

    session.commit()
    return {"status": "created", "template_id": str(template.id)}


@router.put("/templates/{template_id}")
def update_template(
    template_id: UUID,
    payload: TemplateCreate,
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

    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Template name is required.")
    if not payload.sets:
        raise HTTPException(
            status_code=400, detail="Add at least one exercise row to save a template."
        )

    template.name = clean_name
    template.notes = payload.notes.strip() if payload.notes else None

    for existing_set in template.sets:
        session.delete(existing_set)
    session.commit()

    for item in payload.sets:
        session.add(
            TemplateSet(
                template_id=template.id,
                exercise_name=item.exercise_name.strip(),
                set_number=item.set_number,
                weight=str(item.weight),
                weight_unit=item.weight_unit or "lbs",
                reps=item.reps,
                rpe=item.rpe,
                notes=item.notes.strip() if item.notes else None,
            )
        )

    session.commit()
    return {"status": "updated", "template_id": str(template.id)}


@router.get("/templates")
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
                    "weight_unit": s.weight_unit or "lbs",
                    "reps": s.reps,
                    "rpe": s.rpe,
                    "notes": s.notes,
                }
                for s in sorted(t.sets, key=lambda x: x.set_number)
            ],
        }
        for t in templates
    ]


@router.delete("/templates/{template_id}")
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
    resolve_user_split(current_user, session)
    return {"status": "deleted"}


# --- Workout Session Endpoints ---


@router.post("/workouts")
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
                weight=str(item.weight),
                weight_unit=item.weight_unit or "lbs",
                reps=item.reps,
                rpe=item.rpe,
                notes=clean_note or None,
            )
        )

    split_state = resolve_user_split(current_user, session)
    if split_state["template_ids"]:
        current_user.split_index = (split_state["current_index"] + 1) % len(
            split_state["template_ids"]
        )
        session.add(current_user)

    session.commit()
    updated_split = resolve_user_split(current_user, session)
    return {
        "status": "saved",
        "workout_id": str(workout.id),
        "split": updated_split,
    }


@router.put("/workouts/{workout_id}")
def update_workout(
    workout_id: UUID,
    payload: WorkoutCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workout = session.exec(
        select(WorkoutSession).where(
            WorkoutSession.id == workout_id,
            WorkoutSession.user_id == current_user.id,
        )
    ).first()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found.")

    workout.title = payload.title
    workout.notes = payload.notes
    if payload.started_at:
        workout.started_at = payload.started_at
        workout.date = payload.started_at
    if payload.ended_at:
        workout.ended_at = payload.ended_at

    for existing_set in workout.sets:
        session.delete(existing_set)
    session.commit()

    for item in payload.sets:
        clean_note = item.notes.strip() if item.notes else None
        session.add(
            SetLog(
                workout_id=workout.id,
                exercise_name=item.exercise_name,
                set_number=item.set_number,
                weight=str(item.weight),
                weight_unit=item.weight_unit or "lbs",
                reps=item.reps,
                rpe=item.rpe,
                notes=clean_note or None,
            )
        )

    session.commit()
    return {"status": "updated", "workout_id": str(workout.id)}


@router.delete("/workouts/{workout_id}")
def delete_workout(
    workout_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    workout = session.exec(
        select(WorkoutSession).where(
            WorkoutSession.id == workout_id,
            WorkoutSession.user_id == current_user.id,
        )
    ).first()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found.")

    session.delete(workout)
    session.commit()
    return {"status": "deleted"}


@router.get("/workouts")
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
                        "weight_unit": s.weight_unit or "lbs",
                        "reps": s.reps,
                        "rpe": s.rpe,
                        "notes": s.notes,
                    }
                    for s in sorted(w.sets, key=lambda x: x.set_number)
                ],
            }
        )
    return results