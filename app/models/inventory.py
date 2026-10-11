from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"),
        nullable=False,
        unique=True
    )

    available_quantity: Mapped[int] = mapped_column(
        nullable=False,
        default=0
    )

    reserved_quantity: Mapped[int] = mapped_column(
        nullable=False,
        default=0
    )