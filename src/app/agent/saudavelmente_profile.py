"""Load the clinic profile and knowledge base used by Liana."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PROFILE_FILE = "clara-instituto-saudavelmente-configuracao-sdr.json"


@lru_cache(maxsize=1)
def build_saudavelmente_agent_context() -> str:
    """Render trusted identity, clinic facts, boundaries, and voice from the SDR config."""

    config_path = Path.cwd() / PROFILE_FILE
    config = json.loads(config_path.read_text(encoding="utf-8"))
    profile = config["profile"]
    knowledge = "\n".join(
        f"- {entry['title']}: {entry['content']}" for entry in config["knowledgeBase"]
    )
    return (
        "Identidade, limites e base de conhecimento da Liana — Instituto Saudavelmente. "
        "Use estes fatos confirmados; não invente informações que não estejam aqui.\n"
        f"Identidade e oferta:\n{profile['identity']}\n"
        f"Público atendido:\n{profile['icp']}\n"
        f"Limites de segurança:\n{profile['forbiddenTopics']}\n"
        f"Base de conhecimento:\n{knowledge}\n"
        f"Forma de conversar:\n{profile['tone']}"
    )
