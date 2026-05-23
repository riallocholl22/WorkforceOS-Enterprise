import numpy as np
from backend.utils.semantic_engine import get_embedding

# simple in-memory store
VECTOR_DB = []


def add_resume(resume_id: str, text: str, metadata: dict):
    embedding = get_embedding(text)  # numpy array

    VECTOR_DB.append({
        "id": resume_id,
        "embedding": embedding,
        "metadata": metadata
    })


def search_top_k(query: str, top_k: int = 5):
    query_vec = get_embedding(query)

    results = []

    for item in VECTOR_DB:
        emb = item["embedding"]

        score = float(
            np.dot(query_vec, emb) /
            (np.linalg.norm(query_vec) * np.linalg.norm(emb) + 1e-8)
        )

        results.append({
            "id": item["id"],
            "score": round(score, 4),
            "metadata": item["metadata"]
        })

    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:top_k]