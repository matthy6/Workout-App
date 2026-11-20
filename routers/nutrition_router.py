from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from auth import get_current_user
from database import get_session
from models import (
    FoodBankUpsert,
    FoodDatabase,
    FoodLog,
    FoodLogCreate,
    MacroTargetsPayload,
    User,
)

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])


def _clean_macro_val(val: float | None) -> float | None:
    if val is None or val <= 0:
        return None
    return round(float(val), 1)


def _clean_cal_val(val: int | None) -> int | None:
    if val is None or val <= 0:
        return None
    return int(val)


# --- Macro Target Endpoints ---


@router.get("/targets")
def get_macro_targets(current_user: User = Depends(get_current_user)):
    return {
        "target_calories": current_user.target_calories,
        "target_protein": current_user.target_protein,
        "target_carbs": current_user.target_carbs,
        "target_fats": current_user.target_fats,
    }


@router.put("/targets")
def update_macro_targets(
    payload: MacroTargetsPayload,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    current_user.target_calories = _clean_cal_val(payload.target_calories)
    current_user.target_protein = _clean_macro_val(payload.target_protein)
    current_user.target_carbs = _clean_macro_val(payload.target_carbs)
    current_user.target_fats = _clean_macro_val(payload.target_fats)

    session.add(current_user)
    session.commit()
    session.refresh(current_user)

    return {
        "status": "updated",
        "targets": {
            "target_calories": current_user.target_calories,
            "target_protein": current_user.target_protein,
            "target_carbs": current_user.target_carbs,
            "target_fats": current_user.target_fats,
        },
    }


# --- Saved Food Bank Endpoints ---


@router.get("/foods")
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
            "id": str(f.id),
            "food_name": f.food_name,
            "unit": f.default_unit,
            "calories_per_unit": f.calories_per_unit,
            "protein_per_unit": f.protein_per_unit,
            "carbs_per_unit": f.carbs_per_unit,
            "fats_per_unit": f.fats_per_unit,
        }
        for f in foods
    ]


@router.post("/foods", status_code=status.HTTP_201_CREATED)
def create_or_upsert_saved_food(
    payload: FoodBankUpsert,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    clean_name = payload.food_name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Food name is required.")
    if payload.reference_amount <= 0:
        raise HTTPException(status_code=400, detail="Serving amount must be greater than 0.")

    amt = payload.reference_amount
    c_per_u = payload.calories / amt
    p_per_u = (payload.protein or 0.0) / amt
    carb_per_u = (payload.carbs or 0.0) / amt
    f_per_u = (payload.fats or 0.0) / amt
    clean_unit = payload.unit.strip() if payload.unit else None

    existing = session.exec(
        select(FoodDatabase).where(
            FoodDatabase.user_id == current_user.id,
            FoodDatabase.food_name.ilike(clean_name),
        )
    ).first()

    if existing:
        existing.food_name = clean_name
        existing.default_unit = clean_unit
        existing.calories_per_unit = c_per_u
        existing.protein_per_unit = p_per_u
        existing.carbs_per_unit = carb_per_u
        existing.fats_per_unit = f_per_u
        session.add(existing)
        session.commit()
        return {"status": "updated", "id": str(existing.id)}

    new_item = FoodDatabase(
        user_id=current_user.id,
        food_name=clean_name,
        default_unit=clean_unit,
        calories_per_unit=c_per_u,
        protein_per_unit=p_per_u,
        carbs_per_unit=carb_per_u,
        fats_per_unit=f_per_u,
    )
    session.add(new_item)
    session.commit()
    return {"status": "created", "id": str(new_item.id)}


@router.put("/foods/{food_db_id}")
def update_saved_food(
    food_db_id: UUID,
    payload: FoodBankUpsert,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    item = session.exec(
        select(FoodDatabase).where(
            FoodDatabase.id == food_db_id,
            FoodDatabase.user_id == current_user.id,
        )
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Saved food item not found.")

    clean_name = payload.food_name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Food name is required.")
    if payload.reference_amount <= 0:
        raise HTTPException(status_code=400, detail="Serving amount must be greater than 0.")

    amt = payload.reference_amount
    item.food_name = clean_name
    item.default_unit = payload.unit.strip() if payload.unit else None
    item.calories_per_unit = payload.calories / amt
    item.protein_per_unit = (payload.protein or 0.0) / amt
    item.carbs_per_unit = (payload.carbs or 0.0) / amt
    item.fats_per_unit = (payload.fats or 0.0) / amt

    session.add(item)
    session.commit()
    return {"status": "updated", "id": str(item.id)}


@router.delete("/foods/{food_db_id}")
def delete_saved_food(
    food_db_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    item = session.exec(
        select(FoodDatabase).where(
            FoodDatabase.id == food_db_id,
            FoodDatabase.user_id == current_user.id,
        )
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Saved food item not found.")

    session.delete(item)
    session.commit()
    return {"status": "deleted"}


# --- Daily Food Log Endpoints ---


@router.post("", status_code=status.HTTP_201_CREATED)
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
                FoodDatabase.food_name.ilike(clean_name),
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


@router.get("")
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
        "targets": {
            "target_calories": current_user.target_calories,
            "target_protein": current_user.target_protein,
            "target_carbs": current_user.target_carbs,
            "target_fats": current_user.target_fats,
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


@router.get("/history")
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
                "items": [],
            }
        history_map[l.date]["items"].append({
            "id": str(l.id),
            "food_name": l.food_name,
            "amount": l.amount,
            "unit": l.unit,
            "calories": l.calories,
            "protein": l.protein,
            "carbs": l.carbs,
            "fats": l.fats,
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
            "fats": round(total_fat, 1),
        }
        results.append(d_data)

    return results


@router.delete("/{food_id}")
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