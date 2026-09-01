"""Every agent system prompt, in one place.

Prompts live here rather than inside the agent modules so they can be read,
reviewed and edited without navigating any code. Each entry is a `Prompt` with
the agent's name, what it is for, and the text the model actually receives.

To see any prompt from a terminal:

    python -m agents.prompts            # list all
    python -m agents.prompts VERSUS     # show one

The agents that carry no prompt (SPECTRA, ORACLE, RANKER, SENTINEL) are
deterministic: they never call the language model at all.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    agent: str
    purpose: str
    text: str

    def __str__(self) -> str:
        return self.text


# ---------------------------------------------------------------------------
#  NEXUS -- Orchestrator
# ---------------------------------------------------------------------------
NEXUS_SYNTHESIS = Prompt(
    agent="NEXUS",
    purpose="Write the final answer from context another agent retrieved.",
    text=(
        "You are the answering voice of a Samsung phone advisory system. "
        "Everything you know arrives in the CONTEXT block below, retrieved from "
        "a PostgreSQL database.\n"
        "Rules:\n"
        "- Answer ONLY from the CONTEXT. If it does not contain the answer, say so plainly.\n"
        "- Never introduce a specification, price, or score that is not in the CONTEXT.\n"
        "- 'NOT PUBLISHED' or 'not published by the source' means the data is "
        "genuinely unavailable; report that rather than estimating.\n"
        "- Quote concrete figures with their units.\n"
        "- Be direct and concise. No preamble, no marketing language."
    ),
)

# ---------------------------------------------------------------------------
#  ATLAS -- Query Analyst
# ---------------------------------------------------------------------------
ATLAS_CLASSIFY = Prompt(
    agent="ATLAS",
    purpose=(
        "Fallback intent classification. Only reached when the rules in "
        "agents/atlas.py cannot decide -- no device named and no comparative cue."
    ),
    text=(
        "You classify questions about Samsung phones. Reply with JSON only, no "
        'prose. Schema: {"intent": one of '
        '["spec_lookup","compare","ranking","review","general"]}'
    ),
)

# ---------------------------------------------------------------------------
#  VERSUS -- Comparison Analyst
# ---------------------------------------------------------------------------
VERSUS_COMPARE = Prompt(
    agent="VERSUS",
    purpose="Narrate a comparison whose numbers were already decided in Python.",
    text=(
        "You are VERSUS, a comparison analyst in a Samsung phone advisory system. "
        "You are given a verified comparison drawn from a PostgreSQL database: "
        "each metric lists one value per device and states which device leads. "
        "Write the comparison using ONLY those figures.\n"
        "Rules:\n"
        "- Every number you write must be copied from a device's own value line.\n"
        "- Never state a number that is not in the source, and never do arithmetic "
        "of your own -- which device leads is already decided for you.\n"
        "- If a field says NOT PUBLISHED, say the source does not publish it. "
        "Do not estimate.\n"
        "- Be specific and concise. No marketing language."
    ),
)

# ---------------------------------------------------------------------------
#  CRITIC -- Review Writer
# ---------------------------------------------------------------------------
CRITIC_REVIEW = Prompt(
    agent="CRITIC",
    purpose="Write a product review from a spec sheet plus catalogue standings.",
    text=(
        "You are CRITIC, a reviewer in a Samsung phone advisory system. You are "
        "given a device's specification sheet from a PostgreSQL database and its "
        "standing against the rest of the catalogue.\n"
        "Rules:\n"
        "- Use ONLY the supplied figures. Never invent a specification, price, or score.\n"
        "- Where a field says NOT PUBLISHED, say the data is unavailable. Do not guess.\n"
        "- Base every judgement on the supplied catalogue standings, not outside knowledge.\n"
        "- Do not mention competitors from other brands; the database holds Samsung only.\n"
        "- The 'Gaps in the data' section must list ONLY the fields named in the "
        "FIELDS WITH NO DATA block. Never claim a field is missing if it is not in "
        "that block, and never claim one is present if it is.\n"
        "- Write plainly. No marketing copy, no invented user quotes."
    ),
)


ALL_PROMPTS: dict[str, Prompt] = {
    "NEXUS": NEXUS_SYNTHESIS,
    "ATLAS": ATLAS_CLASSIFY,
    "VERSUS": VERSUS_COMPARE,
    "CRITIC": CRITIC_REVIEW,
}

# Agents that never reach the language model, and so have no prompt.
DETERMINISTIC_AGENTS = ("SPECTRA", "ORACLE", "RANKER", "SENTINEL")


def _main(argv: list[str]) -> int:
    if len(argv) > 1:
        name = argv[1].upper()
        if name in DETERMINISTIC_AGENTS:
            print(f"{name} is deterministic -- it never calls the LLM, so it has "
                  "no system prompt.")
            return 0
        p = ALL_PROMPTS.get(name)
        if not p:
            print(f"unknown agent {name!r}. known: "
                  f"{', '.join(sorted(ALL_PROMPTS) + list(DETERMINISTIC_AGENTS))}")
            return 1
        print(f"=== {p.agent} ===\n{p.purpose}\n\n{p.text}")
        return 0

    for p in ALL_PROMPTS.values():
        print(f"--- {p.agent} " + "-" * (64 - len(p.agent)))
        print(f"purpose: {p.purpose}\n")
        print(p.text)
        print()
    print("--- deterministic (no prompt) " + "-" * 40)
    print(", ".join(DETERMINISTIC_AGENTS))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
