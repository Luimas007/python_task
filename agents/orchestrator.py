import re
from agents.spec_agent import SpecAgent
from agents.review_agent import ReviewAgent
from chatbot.llm_client import LLMClient
from rag.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)

CHAT_SYSTEM_PROMPT = (
    "You are a Samsung Galaxy phone assistant. Answer the user's question "
    "using ONLY the phone context provided below. If the context does not "
    "contain the answer, say you don't have that information. Be concise."
)

REVIEW_KEYWORDS = ("review", "worth buying", "should i buy", "recommend")
COMPARE_KEYWORDS = ("compare", "vs", "versus", "better than", "difference between")
PHONE_REFERENCE_PATTERN = re.compile(r"galaxy|samsung|\bs\d{2}\b|note|fold|flip|ultra|plus", re.I)


class Orchestrator:
    """Routes a user query to the right agent(s) and composes the answer.

    Keeps track of the last phone(s) discussed so short follow-up questions
    ("what about its battery?") resolve without the user repeating the model
    name. Single in-memory instance is fine here - this is a local,
    single-user app, not a multi-tenant service.
    """

    def __init__(self):
        self.vector_store = VectorStore()
        self.spec_agent = SpecAgent(self.vector_store)
        self.llm_client = LLMClient()
        self.review_agent = ReviewAgent(self.llm_client)
        self.last_phones: list[dict] = []

    def handle_query(self, query: str) -> dict:
        query_lower = query.lower()
        search_text = self._resolve_search_text(query)

        if any(k in query_lower for k in COMPARE_KEYWORDS):
            result = self._handle_compare(query, search_text)
        elif any(k in query_lower for k in REVIEW_KEYWORDS):
            result = self._handle_review(query, search_text)
        else:
            result = self._handle_spec_question(query, search_text)

        return result

    def _resolve_search_text(self, query: str) -> str:
        """Prepend the last discussed phone name(s) when the query looks
        like a follow-up (no new phone/model reference of its own)."""
        if self.last_phones and not PHONE_REFERENCE_PATTERN.search(query):
            names = " ".join(p["name"] for p in self.last_phones)
            return f"{names}. {query}"
        return query

    def _handle_spec_question(self, query: str, search_text: str) -> dict:
        phones = self.spec_agent.find_relevant_phones(search_text, top_k=3)
        if phones:
            self.last_phones = phones
        context = "\n\n".join(self.vector_store.phone_to_document(p) for p in self.last_phones)
        prompt = f"Context:\n{context}\n\nQuestion: {query}"
        answer = self.llm_client.generate(prompt, system=CHAT_SYSTEM_PROMPT)
        return {"answer": answer, "phones": [p["name"] for p in self.last_phones], "intent": "spec_lookup"}

    def _handle_review(self, query: str, search_text: str) -> dict:
        phones = self.spec_agent.find_relevant_phones(search_text, top_k=1)
        if phones:
            self.last_phones = phones
        if not self.last_phones:
            return {"answer": "I couldn't identify which phone you mean.", "phones": [], "intent": "review"}
        review = self.review_agent.generate_review(self.last_phones[0])
        return {"answer": review, "phones": [self.last_phones[0]["name"]], "intent": "review"}

    def _handle_compare(self, query: str, search_text: str) -> dict:
        phones = self.spec_agent.find_relevant_phones(search_text, top_k=2)
        if len(phones) >= 2:
            self.last_phones = phones
        if len(self.last_phones) < 2:
            return {
                "answer": "I need two specific phone models to compare.",
                "phones": [p["name"] for p in self.last_phones],
                "intent": "compare",
            }
        answer = self.review_agent.compare(self.last_phones[0], self.last_phones[1])
        return {"answer": answer, "phones": [p["name"] for p in self.last_phones], "intent": "compare"}
