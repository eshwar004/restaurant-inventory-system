from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.inventory import Inventory

router = APIRouter(
    prefix="/inventory",
    tags=["Inventory"]
)


@router.post("/")
def create_inventory( product_id: int, available_quantity: int, db: Session = Depends(get_db) ):
    inventory = Inventory(
        product_id=product_id,
        available_quantity=available_quantity,
        reserved_quantity=0
    )

    db.add(inventory)
    db.commit()
    db.refresh(inventory)

    return inventory