from pydantic import BaseModel
from typing import Optional, List


class RankRequest(BaseModel):
    job_description: str
    top_k: int = 5
    min_experience: Optional[int] = 0
    required_skills: Optional[List[str]] = []
