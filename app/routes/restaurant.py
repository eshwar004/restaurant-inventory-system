
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.cache import get_cache, set_cache, delete_cache
from app.db.dependencies import get_db
from app.models.restaurant import Restaurant

router = APIRouter(
    prefix="/restaurants",
    tags=["Restaurants"]
)

RESTAURANTS_CACHE_KEY = "restaurants:all"


@router.post("/")
def create_restaurant(
    name: str,
    address: str,
    db: Session = Depends(get_db),
):
    restaurant = Restaurant(name=name, address=address)

    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)

    # Invalidate cached restaurant list after successful commit.
    delete_cache(RESTAURANTS_CACHE_KEY)

    return restaurant


@router.get("/")
def get_restaurants(db: Session = Depends(get_db)):
    cached_restaurants = get_cache(RESTAURANTS_CACHE_KEY)

    if cached_restaurants is not None:
        return cached_restaurants

    restaurants = db.query(Restaurant).order_by(Restaurant.id).all()
    result = jsonable_encoder(restaurants)

    set_cache(RESTAURANTS_CACHE_KEY, result, ttl=60)

    return result
