import unittest

from backend.services.matcher import rank_candidates_tfidf, tfidf_cosine_score


class TfidfMatchingTests(unittest.TestCase):
    def test_tfidf_cosine_score_rewards_relevant_resume(self):
        job = "We need a Python FastAPI engineer with SQL and machine learning experience."
        relevant_resume = "Python developer building FastAPI APIs with SQL databases and ML models."
        unrelated_resume = "Graphic designer focused on typography, branding, and print campaigns."

        relevant_score = tfidf_cosine_score(job, relevant_resume, ["python", "fastapi", "sql"])
        unrelated_score = tfidf_cosine_score(job, unrelated_resume, [])

        self.assertGreater(relevant_score, unrelated_score)
        self.assertGreater(relevant_score, 0)

    def test_rank_candidates_tfidf_orders_best_match_first(self):
        job = "Hiring a backend engineer skilled in Python, FastAPI, SQL, and Docker."
        candidates = [
            {
                "candidate_id": "designer",
                "text": "Brand designer with illustration and visual identity experience.",
                "skills": ["branding"],
            },
            {
                "candidate_id": "backend",
                "text": "Backend engineer using Python, FastAPI, SQL, Docker, and REST APIs.",
                "skills": ["python", "fastapi", "sql", "docker"],
            },
        ]

        ranked = rank_candidates_tfidf(job, candidates)

        self.assertEqual(ranked[0]["candidate_id"], "backend")
        self.assertGreater(ranked[0]["tfidf_score"], ranked[1]["tfidf_score"])
        self.assertEqual(ranked[0]["score"], ranked[0]["match_score"])
        self.assertGreaterEqual(ranked[0]["match_score"], 0)
        self.assertLessEqual(ranked[0]["match_score"], 100)
        self.assertEqual(ranked[0]["matched_skills"], ["docker", "fastapi", "python", "sql"])


if __name__ == "__main__":
    unittest.main()
