from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime, func
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Phone(Base):
    __tablename__ = "phones"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    model_code = Column(String(50))
    release_year = Column(Integer)
    price_usd = Column(Float)
    source_url = Column(String(255))

    specification = relationship(
        "Specification", back_populates="phone", uselist=False,
        cascade="all, delete-orphan"
    )
    reviews = relationship(
        "Review", back_populates="phone", cascade="all, delete-orphan"
    )


class Specification(Base):
    __tablename__ = "specifications"

    id = Column(Integer, primary_key=True)
    phone_id = Column(Integer, ForeignKey("phones.id"), unique=True, nullable=False)

    display_size = Column(String(50))
    display_type = Column(String(100))
    resolution = Column(String(50))
    chipset = Column(String(100))
    ram = Column(String(50))
    storage = Column(String(100))
    battery_capacity = Column(String(50))
    charging = Column(String(100))
    camera_main = Column(String(150))
    camera_ultrawide = Column(String(150))
    camera_telephoto = Column(String(150))
    camera_front = Column(String(150))
    os = Column(String(100))
    weight = Column(String(50))
    dimensions = Column(String(100))

    phone = relationship("Phone", back_populates="specification")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    phone_id = Column(Integer, ForeignKey("phones.id"), nullable=False)
    review_text = Column(Text, nullable=False)
    generated_at = Column(DateTime, server_default=func.now())

    phone = relationship("Phone", back_populates="reviews")
