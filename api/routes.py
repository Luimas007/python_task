from fastapi import APIRouter, HTTPException
from agents.orchestrator import Orchestrator
from database.phone_service import PhoneService
from api.schemas import ChatRequest, ChatResponse, ReviewResponse
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
orchestrator = Orchestrator()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/phones")
def list_phones():
    return PhoneService.list_phones()


@router.get("/phones/{phone_id}")
def get_phone(phone_id: int):
    phone = PhoneService.get_phone(phone_id)
    if not phone:
        raise HTTPException(status_code=404, detail="Phone not found")
    return phone


@router.post("/phones/{phone_id}/review", response_model=ReviewResponse)
def generate_review(phone_id: int):
    phone = PhoneService.get_phone(phone_id)
    if not phone:
        raise HTTPException(status_code=404, detail="Phone not found")
    review = orchestrator.review_agent.generate_review(phone)
    return ReviewResponse(phone=phone["name"], review=review)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    logger.info(f"Chat query: {request.query}")
    result = orchestrator.handle_query(request.query)
    return ChatResponse(**result)
