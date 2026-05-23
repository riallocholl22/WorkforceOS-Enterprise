import re
from typing import List

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None
try:
    import pdfplumber
except Exception:  # pragma: no cover - optional dependency
    pdfplumber = None

from backend.services.skill_extractor import SKILL_KEYWORDS, extract_skills

# ---------------- TEXT EXTRACTION ---------------- #

def extract_text(file_path: str) -> str:
    text = extract_text_with_pypdf2(file_path)

    if text:
        return text

    return extract_text_with_pdfplumber(file_path)


def extract_text_with_pypdf2(file_path: str) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(file_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception as e:
        print("PyPDF2 extraction error:", e)
        return ""


def extract_text_with_pdfplumber(file_path: str) -> str:
    if pdfplumber is None:
        return ""
    try:
        with pdfplumber.open(file_path) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

        return text.strip()

    except Exception as e:
        print("PDF extraction error:", e)
        return ""


# ---------------- CLEAN TEXT ---------------- #

def clean_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9+#.\s-]", " ", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------- SKILL EXTRACTION ---------------- #

def extract_skills_legacy(text: str) -> List[str]:
    return extract_skills(text)
