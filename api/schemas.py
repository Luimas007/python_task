from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str


class ChatResponse(BaseModel):
    answer: str
    phones: list[str]
    intent: str


class ReviewResponse(BaseModel):
    phone: str
    review: str
