import faiss
import numpy as np
import os
import pickle

# -------------------------
# CONFIG
# -------------------------
DIMENSION = 384

INDEX_PATH = "backend/storage/faiss.index"
META_PATH = "backend/storage/meta.pkl"
MAP_PATH = "backend/storage/id_map.pkl"

os.makedirs("backend/storage", exist_ok=True)

# -------------------------
# INIT INDEX (COSINE)
# -------------------------
index = faiss.IndexFlatIP(DIMENSION)

id_store = []
metadata_store = []
id_map = {}


# -------------------------
# NORMALIZE
# -------------------------
def _normalize(v):
    v = np.array(v).astype("float32")
    return v / (np.linalg.norm(v) + 1e-10)


# -------------------------
# SAVE STATE (PERSISTENCE)
# -------------------------
def save_store():
    faiss.write_index(index, INDEX_PATH)

    with open(META_PATH, "wb") as f:
        pickle.dump(metadata_store, f)

    with open(MAP_PATH, "wb") as f:
        pickle.dump((id_store, id_map), f)


# -------------------------
# LOAD STATE
# -------------------------
def load_store():
    global index, metadata_store, id_store, id_map

    if os.path.exists(INDEX_PATH):
        index = faiss.read_index(INDEX_PATH)

    if os.path.exists(META_PATH):
        with open(META_PATH, "rb") as f:
            metadata_store = pickle.load(f)

    if os.path.exists(MAP_PATH):
        with open(MAP_PATH, "rb") as f:
            id_store, id_map = pickle.load(f)


# -------------------------
# ADD CANDIDATE
# -------------------------
def add_candidate(candidate_id, text, metadata, embedding):

    global index, id_store, metadata_store, id_map

    if candidate_id in id_map:
        return

    embedding = _normalize(embedding).reshape(1, -1)

    index.add(embedding)

    idx = len(id_store)

    id_store.append(candidate_id)

    metadata_store.append({
        **metadata,
        "text": text
    })

    id_map[candidate_id] = idx

    save_store()


# -------------------------
# RETRIEVE
# -------------------------
def retrieve_candidates(query_embedding, top_k=10):

    query_embedding = _normalize(query_embedding).reshape(1, -1)

    scores, indices = index.search(query_embedding, top_k)

    results = []

    for i, idx in enumerate(indices[0]):
        if idx == -1:
            continue

        meta = metadata_store[idx]

        results.append({
            "candidate_id": id_store[idx],
            "score": float(scores[0][i]),
            "skills": meta.get("skills"),
            "role": meta.get("role"),
            "experience": meta.get("experience"),
            "text_snippet": meta.get("text", "")[:300]
        })

    return results