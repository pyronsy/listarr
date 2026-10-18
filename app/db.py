import os

from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DATA_DIR}/listarr.db",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class MediaList(Base):
    __tablename__ = "lists"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, default="")
    items = relationship(
        "Item",
        back_populates="media_list",
        cascade="all, delete-orphan",
        order_by="Item.id",
    )


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    list_id = Column(Integer, ForeignKey("lists.id"), nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    media_type = Column(String, nullable=False, default="movie")  # movie | show
    media_list = relationship("MediaList", back_populates="items")


def init_db():
    Base.metadata.create_all(engine)
