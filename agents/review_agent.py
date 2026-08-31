from chatbot.llm_client import LLMClient
from database.phone_service import PhoneService
from utils.logger import get_logger

logger = get_logger(__name__)

REVIEW_SYSTEM_PROMPT = (
    "You are a professional smartphone reviewer. Write a concise, factual "
    "product review (150-250 words) based only on the specifications given. "
    "Cover display, performance, camera, battery and who the phone suits. "
    "Do not invent specs that were not provided."
)


class ReviewAgent:
    """Generates a review by combining a phone's specifications via the LLM."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def generate_review(self, phone: dict, persist: bool = True) -> str:
        spec_lines = "\n".join(
            f"- {k.replace('_', ' ')}: {v}" for k, v in phone.get("specification", {}).items() if v
        )
        prompt = (
            f"Phone: {phone['name']} (released {phone.get('release_year')}, "
            f"price ${phone.get('price_usd')})\nSpecifications:\n{spec_lines}\n\n"
            "Write the review now."
        )
        review_text = self.llm_client.generate(prompt, system=REVIEW_SYSTEM_PROMPT, max_tokens=350)

        if persist:
            PhoneService.save_review(phone["id"], review_text)
            logger.info(f"Review generated and saved for phone_id={phone['id']}")

        return review_text

    def compare(self, phone_a: dict, phone_b: dict) -> str:
        prompt = (
            f"Compare these two phones for a buyer:\n\n"
            f"Phone A: {phone_a['name']} - {phone_a.get('specification')}\n\n"
            f"Phone B: {phone_b['name']} - {phone_b.get('specification')}\n\n"
            "Give a short structured comparison (performance, camera, battery, "
            "value) and a final recommendation."
        )
        return self.llm_client.generate(
            prompt, system="You are a helpful, factual smartphone comparison assistant."
        )
