
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.events import publish_order_event

from app.db.dependencies import get_db
from app.models.inventory import Inventory
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.product import Product
from app.models.reservation import Reservation
from app.schemas.order import CreateOrderRequest

router = APIRouter(
    prefix="/orders",
    tags=["Orders"]
)


def calculate_request_hash(request: CreateOrderRequest) -> str:
    # Sort items so their order in the JSON array doesn't matter.
    items = sorted(
        [
            {
                "product_id": item.product_id,
                "quantity": item.quantity
            }
            for item in request.items
        ],
        key=lambda item: item["product_id"]
    )

    payload = {
        "restaurant_id": request.restaurant_id,
        "items": items
    }

    canonical_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":")
    )

    return hashlib.sha256(
        canonical_payload.encode("utf-8")
    ).hexdigest()


def get_order_response(db: Session, order: Order):
    items = db.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id)
    ).all()

    reservations = db.scalars(
        select(Reservation).where(Reservation.order_id == order.id)
    ).all()

    return {
        "order_id": order.id,
        "restaurant_id": order.restaurant_id,
        "status": order.status,
        "total_amount": str(order.total_amount),
        "items": [
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price)
            }
            for item in items
        ],
        "reservations": [
            {
                "inventory_id": reservation.inventory_id,
                "quantity": reservation.quantity,
                "status": reservation.status,
                "expires_at": reservation.expires_at.isoformat()
            }
            for reservation in reservations
        ]
    }


def get_existing_order(
    db: Session,
    idempotency_key: str,
    request_hash: str
):
    existing_order = db.scalar(
        select(Order).where(
            Order.idempotency_key == idempotency_key
        )
    )

    if existing_order is None:
        return None

    if existing_order.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail="Idempotency key reused with a different request"
        )

    return get_order_response(db, existing_order)


@router.post("/")
def create_order(
    request: CreateOrderRequest,
    db: Session = Depends(get_db)
):
    request_hash = calculate_request_hash(request)

    try:
        # 1. Return an existing order for a matching retry.
        existing_response = get_existing_order(
            db,
            request.idempotency_key,
            request_hash
        )

        if existing_response is not None:
            return existing_response

        # 2. Reject repeated product IDs within one order.
        product_ids = sorted(
            item.product_id for item in request.items
        )

        if len(product_ids) != len(set(product_ids)):
            raise HTTPException(
                status_code=400,
                detail="A product can appear only once in an order"
            )

        # 3. Insert the order and claim the unique key.
        order = Order(
            restaurant_id=request.restaurant_id,
            total_amount=Decimal("0.00"),
            status="PENDING",
            idempotency_key=request.idempotency_key,
            request_hash=request_hash
        )

        db.add(order)
        db.flush()

        total_amount = Decimal("0.00")

        # 4. Lock inventory rows in ascending product ID order.
        for product_id in product_ids:
            product = db.scalar(
                select(Product).where(
                    Product.id == product_id,
                    Product.restaurant_id == request.restaurant_id
                )
            )

            if product is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Product {product_id} not found in this restaurant"
                )

            inventory = db.scalar(
                select(Inventory)
                .where(Inventory.product_id == product_id)
                .with_for_update()
            )

            if inventory is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Inventory not found for product {product_id}"
                )

            requested_quantity = next(
                item.quantity
                for item in request.items
                if item.product_id == product_id
            )

            if inventory.available_quantity < requested_quantity:
                raise HTTPException(
                    status_code=409,
                    detail=f"Insufficient inventory for product {product_id}"
                )

            inventory.available_quantity -= requested_quantity
            inventory.reserved_quantity += requested_quantity


            total_amount += product.price * requested_quantity

            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=requested_quantity,
                    unit_price=product.price
                )
            )

            db.add(
                Reservation(
                    order_id=order.id,
                    inventory_id=inventory.id,
                    quantity=requested_quantity,
                    status="ACTIVE",
                    expires_at=datetime.utcnow() + timedelta(minutes=15)
                )
            )

        order.total_amount = total_amount

        # 5. Save order, items, reservations and inventory atomically.
        db.commit()
        db.refresh(order)

        response = get_order_response(db, order)

        # Publish an event only for a newly created order.
        event = {
            "event_type": "ORDER_CREATED",
            "order_id": order.id,
            "restaurant_id": order.restaurant_id,
            "status": order.status,
            "total_amount": str(order.total_amount),
            "created_at": datetime.utcnow().isoformat(),
        }

        publish_order_event(event)

        return response

    except IntegrityError:
        # A simultaneous request may have claimed this key first.
        db.rollback()

        existing_response = get_existing_order(
            db,
            request.idempotency_key,
            request_hash
        )

        if existing_response is not None:
            return existing_response

        # It was another integrity error, not an idempotency conflict.
        raise

    except Exception:
        db.rollback()
        raise


@router.post("/expire-reservations")
def expire_reservations(db: Session = Depends(get_db)):
    now = datetime.utcnow()

    try:
        # Find expired reservations that are still active.
        reservations = db.scalars(
            select(Reservation)
            .where(
                Reservation.status == "ACTIVE",
                Reservation.expires_at <= now
            )
            .order_by(Reservation.inventory_id, Reservation.id)
            .with_for_update(skip_locked=True)
        ).all()

        expired_count = 0

        for reservation in reservations:
            # Lock the inventory row before returning stock.
            inventory = db.scalar(
                select(Inventory)
                .where(Inventory.id == reservation.inventory_id)
                .with_for_update()
            )

            if inventory is None:
                continue

            inventory.reserved_quantity -= reservation.quantity
            inventory.available_quantity += reservation.quantity
            reservation.status = "EXPIRED"

            order = db.scalar(
                select(Order)
                .where(Order.id == reservation.order_id)
                .with_for_update()
            )

            if order is not None:
                remaining_active = db.scalar(
                    select(Reservation.id)
                    .where(
                        Reservation.order_id == order.id,
                        Reservation.status == "ACTIVE",
                        Reservation.id != reservation.id
                    )
                    .limit(1)
                )

                if remaining_active is None and order.status == "PENDING":
                    order.status = "EXPIRED"

            expired_count += 1

        db.commit()

        return {
            "expired_reservations": expired_count,
            "processed_at": now.isoformat()
        }

    except Exception:
        db.rollback()
        raise
