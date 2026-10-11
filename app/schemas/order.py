from pydantic import BaseModel, Field


class OrderItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class CreateOrderRequest(BaseModel):
    restaurant_id: int
    items: list[OrderItemRequest] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=100)