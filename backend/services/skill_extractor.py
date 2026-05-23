import re


SKILL_KEYWORDS = [
    "python",
    "fastapi",
    "flask",
    "django",
    "java",
    "spring",
    "go",
    "golang",
    "c#",
    ".net",
    "machine learning",
    "deep learning",
    "nlp",
    "natural language processing",
    "llm",
    "rag",
    "openai",
    "langchain",
    "sql",
    "postgresql",
    "mysql",
    "sqlite",
    "redis",
    "mongodb",
    "snowflake",
    "bigquery",
    "dbt",
    "airflow",
    "pandas",
    "numpy",
    "scikit learn",
    "tensorflow",
    "pytorch",
    "docker",
    "kubernetes",
    "terraform",
    "jenkins",
    "github actions",
    "ci cd",
    "aws",
    "azure",
    "gcp",
    "serverless",
    "javascript",
    "typescript",
    "react",
    "next js",
    "vue",
    "angular",
    "node js",
    "html",
    "css",
    "data analysis",
    "api",
    "rest",
    "graphql",
    "microservices",
    "git",
    "security",
    "oauth",
    "sso",
    "observability",
    "prometheus",
    "grafana",
]

SKILL_ALIASES = {
    "node.js": "node js",
    "node": "node js",
    "nodejs": "node js",
    "sklearn": "scikit learn",
    "scikit-learn": "scikit learn",
    "js": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "ml": "machine learning",
    "ci/cd": "ci cd",
    "cicd": "ci cd",
    "next.js": "next js",
    "google cloud": "gcp",
    "k8s": "kubernetes",
    "tf": "terraform",
    "genai": "llm",
}


def _normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9+#.\s-]", " ", text)
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_skills(text: str) -> list[str]:
    normalized_text = _normalize_text(text)
    found = set()

    searchable_text = f" {normalized_text} "

    for alias, canonical in SKILL_ALIASES.items():
        alias_pattern = r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])"
        if re.search(alias_pattern, normalized_text):
            found.add(canonical)

    for skill in SKILL_KEYWORDS:
        skill_pattern = r"(?<![a-z0-9])" + re.escape(skill.lower()) + r"(?![a-z0-9])"
        if re.search(skill_pattern, searchable_text):
            found.add(skill)

    return sorted(found)
