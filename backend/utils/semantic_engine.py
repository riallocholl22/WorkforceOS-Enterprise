from sentence_transformers import SentenceTransformer
import numpy as np

# Load model once (important for performance)
_model = SentenceTransformer("all-MiniLM-L6-v2")


def get_embedding(text: str):
    """
    Returns embedding as numpy array (NOT torch tensor)
    """
    if not text:
        return np.zeros(384)

    embedding = _model.encode(text, normalize_embeddings=True)
    return embedding  # numpy array


def semantic_similarity(text1: str, text2: str) -> float:
    """
    Cosine similarity between two texts
    """
    if not text1 or not text2:
        return 0.0

    emb1 = get_embedding(text1)
    emb2 = get_embedding(text2)

    # cosine similarity
    dot = np.dot(emb1, emb2)
    norm = np.linalg.norm(emb1) * np.linalg.norm(emb2)

    return float(dot / norm) if norm != 0 else 0.0