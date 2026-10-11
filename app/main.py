from fastapi import FastAPI

from app.routes.restaurant import router as restaurant_router
from app.routes.product import router as product_router
from app.routes.inventory import router as inventory_router
from app.routes.order import router as order_router

app = FastAPI(
    title="Restaurant Inventory & Order Management System"
)

app.include_router(restaurant_router)
app.include_router(product_router)
app.include_router(inventory_router)
app.include_router(order_router)


@app.get("/")
def root():
    return {
        "message": "Restaurant Inventory System is running"
    }