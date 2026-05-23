import hashlib
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.db.database import SessionLocal
from backend.db.models import Candidate


VECTOR_SIZE = int(os.getenv("LOCAL_EMBEDDING_DIMENSIONS", "96"))


def vector_provider_status() -> Dict[str, Any]:
    provider = os.getenv("VECTOR_PROVIDER", "local_hash").lower()
    return {
        "active_provider": provider,
        "configured": {
            "local_hash": True,
            "pgvector": bool(os.getenv("PGVECTOR_DATABASE_URL") or os.getenv("POSTGRES_URL")),
            "pinecone": bool(os.getenv("PINECONE_API_KEY")),
            "chromadb": bool(os.getenv("CHROMA_URL")),
            "weaviate": bool(os.getenv("WEAVIATE_URL") and os.getenv("WEAVIATE_API_KEY")),
        },
        "dimensions": VECTOR_SIZE,
        "storage": "provider_adapter_required" if provider != "local_hash" else "computed_on_read",
    }


def embed_text(text: str) -> List[float]:
    vector = [0.0] * VECTOR_SIZE
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % VECTOR_SIZE
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def semantic_candidate_search(
    query: str,
    *,
    organization_id: Optional[int],
    limit: int = 10,
) -> Dict[str, Any]:
    query_vector = embed_text(query)
    db = SessionLocal()
    try:
        rows = db.query(Candidate)
        if organization_id is not None:
            rows = rows.filter(Candidate.organization_id == organization_id)
        candidates = rows.limit(250).all()
        ranked = []
        query_terms = set(_tokens(query))
        for candidate in candidates:
            text = " ".join([
                candidate.candidate_id or "",
                candidate.role or "",
                candidate.text or "",
                " ".join(candidate.skills or []),
            ])
            score = max(0.0, cosine_similarity(query_vector, embed_text(text)))
            skill_hits = sorted(query_terms.intersection({skill.lower() for skill in candidate.skills or []}))
            ranked.append({
                "candidate_id": candidate.candidate_id,
                "database_id": candidate.id,
                "role": candidate.role,
                "semantic_score": round(score * 100, 2),
                "confidence": "high" if score > 0.35 else "medium" if score > 0.18 else "exploratory",
                "matched_skills": skill_hits,
                "explanation": _explain_candidate(score, skill_hits),
            })
        ranked.sort(key=lambda item: item["semantic_score"], reverse=True)
        return {
            "query": query,
            "provider": vector_provider_status(),
            "results": ranked[:limit],
            "total_scanned": len(candidates),
        }
    finally:
        db.close()


def _tokens(text: str) -> List[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", text or "")][:1200]


def _explain_candidate(score: float, skill_hits: list[str]) -> str:
    if score > 0.35:
        base = "Strong semantic alignment with the recruiter query."
    elif score > 0.18:
        base = "Partial semantic alignment; review experience details before shortlisting."
    else:
        base = "Exploratory match surfaced for hidden-candidate discovery."
    if skill_hits:
        return f"{base} Matched normalized skills: {', '.join(skill_hits[:8])}."
    return base
