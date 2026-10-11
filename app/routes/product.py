
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.cache import get_cache, set_cache, delete_cache
from app.db.dependencies import get_db
from app.models.product import Product

router = APIRouter(
    prefix="/products",
    tags=["Products"]
)

PRODUCTS_CACHE_KEY = "products:all"


@router.post("/")
def create_product(
    restaurant_id: int,
    name: str,
    price: Decimal,
    description: str | None = None,
    db: Session = Depends(get_db),
):
    product = Product(
        restaurant_id=restaurant_id,
        name=name,
        description=description,
        price=price,
    )

    db.add(product)
    db.commit()
    db.refresh(product)

    # Invalidate cached product list after successful commit.
    delete_cache(PRODUCTS_CACHE_KEY)

    return product


@router.get("/")
def get_products(db: Session = Depends(get_db)):
    cached_products = get_cache(PRODUCTS_CACHE_KEY)

    if cached_products is not None:
        return cached_products

    products = db.query(Product).order_by(Product.id).all()
    result = jsonable_encoder(products)

    set_cache(PRODUCTS_CACHE_KEY, result, ttl=60)

    return result
