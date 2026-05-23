from sentence_transformers import SentenceTransformer
from typing import List
import numpy as np

# ✅ LOAD MODEL ONCE (GOOD PRACTICE)
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


# ---------------- EMBEDDING ---------------- #

def get_embedding(text: str):
    if not text:
        return []

    try:
        return model.encode(text, normalize_embeddings=True)
    except Exception as e:
        print("Embedding error:", e)
        return []


# ---------------- BATCH EMBEDDING ---------------- #

def get_embeddings(texts: List[str]):
    if not texts:
        return []

    try:
        return model.encode(texts, normalize_embeddings=True)
    except Exception as e:
        print("Batch embedding error:", e)
        return []


# ---------------- SIMILARITY ---------------- #

def cosine_similarity(vec1, vec2):
    if len(vec1) == 0 or len(vec2) == 0:
        return 0.0

    return float(np.dot(vec1, vec2))


# ---------------- MATCH SCORE ---------------- #

def compute_match_score(text1: str, text2: str):
    emb1 = get_embedding(text1)
    emb2 = get_embedding(text2)

    return cosine_similarity(emb1, emb2)