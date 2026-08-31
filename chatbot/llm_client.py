import ollama
from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    def __init__(self):
        self.client = ollama.Client(host=settings.ollama_host)
        self.model = settings.ollama_model

    def generate(self, prompt: str, system: str = "", max_tokens: int = 300) -> str:
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                options={"num_predict": max_tokens},
            )
            return response["message"]["content"]
        except Exception as exc:
            logger.error(f"LLM generation failed: {exc}")
            return (
                "The local LLM (Ollama) is unreachable. Make sure Ollama is "
                f"running and the model '{self.model}' is pulled "
                f"(ollama pull {self.model})."
            )

    def is_reachable(self) -> bool:
        try:
            self.client.list()
            return True
        except Exception:
            return False
