from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from backend.db.database import Base

class Report(Base):
    __tablename__ = "reports"

    # ✅ PRIMARY KEY
    id = Column(Integer, primary_key=True, index=True)

    # ✅ RELATION (job reference)
    job_id = Column(Integer, index=True, nullable=False)

    # ✅ FILE STORAGE
    file_path = Column(String, nullable=False)
    file_name = Column(String, nullable=True)

    # ✅ TIMESTAMP
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Report id={self.id} job_id={self.job_id}>"