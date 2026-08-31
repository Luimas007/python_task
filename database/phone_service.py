from typing import Optional
from database.db import get_session
from database.models import Phone, Specification, Review
from utils.logger import get_logger

logger = get_logger(__name__)


class PhoneService:
    """Data-access service layer for phone records."""

    @staticmethod
    def upsert_phone(data: dict) -> int:
        with get_session() as session:
            phone = session.query(Phone).filter_by(name=data["name"]).first()
            if not phone:
                phone = Phone(
                    name=data["name"],
                    model_code=data.get("model_code"),
                    release_year=data.get("release_year"),
                    price_usd=data.get("price_usd"),
                    source_url=data.get("source_url"),
                )
                session.add(phone)
                session.flush()
            else:
                phone.model_code = data.get("model_code", phone.model_code)
                phone.release_year = data.get("release_year", phone.release_year)
                phone.price_usd = data.get("price_usd", phone.price_usd)
                phone.source_url = data.get("source_url", phone.source_url)

            spec_data = data.get("specification", {})
            if phone.specification:
                for key, value in spec_data.items():
                    setattr(phone.specification, key, value)
            else:
                phone.specification = Specification(**spec_data)

            session.flush()
            phone_id = phone.id
            logger.info(f"Upserted phone: {phone.name} (id={phone_id})")
            return phone_id

    @staticmethod
    def list_phones() -> list[dict]:
        with get_session() as session:
            phones = session.query(Phone).all()
            return [PhoneService._serialize(p) for p in phones]

    @staticmethod
    def get_phone(phone_id: int) -> Optional[dict]:
        with get_session() as session:
            phone = session.get(Phone, phone_id)
            return PhoneService._serialize(phone) if phone else None

    @staticmethod
    def get_phone_by_name(name: str) -> Optional[dict]:
        with get_session() as session:
            phone = (
                session.query(Phone)
                .filter(Phone.name.ilike(f"%{name}%"))
                .first()
            )
            return PhoneService._serialize(phone) if phone else None

    @staticmethod
    def save_review(phone_id: int, review_text: str) -> int:
        with get_session() as session:
            review = Review(phone_id=phone_id, review_text=review_text)
            session.add(review)
            session.flush()
            return review.id

    @staticmethod
    def get_latest_review(phone_id: int) -> Optional[str]:
        with get_session() as session:
            review = (
                session.query(Review)
                .filter_by(phone_id=phone_id)
                .order_by(Review.generated_at.desc())
                .first()
            )
            return review.review_text if review else None

    @staticmethod
    def _serialize(phone: Phone) -> dict:
        if not phone:
            return {}
        spec = phone.specification
        return {
            "id": phone.id,
            "name": phone.name,
            "model_code": phone.model_code,
            "release_year": phone.release_year,
            "price_usd": phone.price_usd,
            "source_url": phone.source_url,
            "specification": {
                "display_size": spec.display_size if spec else None,
                "display_type": spec.display_type if spec else None,
                "resolution": spec.resolution if spec else None,
                "chipset": spec.chipset if spec else None,
                "ram": spec.ram if spec else None,
                "storage": spec.storage if spec else None,
                "battery_capacity": spec.battery_capacity if spec else None,
                "charging": spec.charging if spec else None,
                "camera_main": spec.camera_main if spec else None,
                "camera_ultrawide": spec.camera_ultrawide if spec else None,
                "camera_telephoto": spec.camera_telephoto if spec else None,
                "camera_front": spec.camera_front if spec else None,
                "os": spec.os if spec else None,
                "weight": spec.weight if spec else None,
                "dimensions": spec.dimensions if spec else None,
            } if spec else {},
        }
