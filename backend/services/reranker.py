# 📁 backend/services/reranker.py

from sentence_transformers import CrossEncoder

# 🔥 LOAD MODEL ONCE (GLOBAL)
model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


def rerank(query: str, candidates: list):

    if not candidates:
        return []

    try:
        # ✅ SAFE TEXT EXTRACTION
        pairs = [
            (query, c.get("text") or c.get("text_snippet") or "")
            for c in candidates
        ]

        # 🔥 LIMIT INPUT SIZE (PERFORMANCE SAFE)
        pairs = pairs[:50]
        candidates = candidates[:50]

        # 🔥 GET SCORES
        scores = model.predict(pairs)

        # ✅ ATTACH SCORES
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)

        # 🔥 SORT DESC
        ranked = sorted(
            candidates,
            key=lambda x: x["rerank_score"],
            reverse=True
        )

        return ranked

    except Exception as e:
        print("❌ Reranker error:", e)

        # 🔥 FALLBACK (RETURN ORIGINAL)
        return candidates