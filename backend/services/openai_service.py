import os
from typing import Optional, Dict, Any, List
import json
from dotenv import load_dotenv

try:
    import openai
except ImportError:  # pragma: no cover - service falls back when dependency is absent
    openai = None

# Load environment variables
load_dotenv()

class OpenAIService:
    """OpenAI service for AI-powered interview evaluation and chat"""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = "gpt-4o-mini"  # Cost-effective model for interviews

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        if openai is None:
            raise ValueError("openai package is not installed")

        # Initialize OpenAI client
        self.client = openai.OpenAI(api_key=self.api_key)

    def generate_response(self, prompt: str, max_tokens: int = 1000,
                         temperature: float = 0.7) -> str:
        """Generate a response from GPT"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert technical interviewer and recruitment consultant. Provide clear, fair, and constructive evaluations."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )

            return response.choices[0].message.content.strip()

        except openai.APIError as e:
            raise Exception(f"OpenAI API error: {str(e)}")
        except openai.RateLimitError as e:
            raise Exception(f"OpenAI rate limit exceeded: {str(e)}")
        except Exception as e:
            raise Exception(f"OpenAI service error: {str(e)}")

    def evaluate_answer(self, question: str, answer: str,
                       context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Evaluate a candidate's answer using GPT"""

        context_str = ""
        if context:
            context_str = f"\nContext: {json.dumps(context)}"

        prompt = f"""
You are evaluating a candidate's interview response. Be fair, constructive, and specific.

QUESTION: {question}

CANDIDATE ANSWER: {answer}{context_str}

Please provide a JSON evaluation with:
- score: number 1-10 (10 being excellent)
- feedback: brief summary (2-3 sentences)
- strengths: array of 2-3 key strengths
- improvements: array of 1-2 areas for improvement
- keywords_demonstrated: array of technical concepts shown

Format as valid JSON only.
"""

        response = self.generate_response(prompt, max_tokens=800, temperature=0.3)

        try:
            # Clean response if it has markdown formatting
            if response.startswith("```json"):
                response = response[7:]
            if response.endswith("```"):
                response = response[:-3]

            evaluation = json.loads(response.strip())

            # Validate required fields
            required_fields = ["score", "feedback", "strengths", "improvements"]
            for field in required_fields:
                if field not in evaluation:
                    evaluation[field] = "Evaluation incomplete"

            # Ensure score is numeric
            if not isinstance(evaluation.get("score"), (int, float)):
                evaluation["score"] = 5  # Default score

            return evaluation

        except json.JSONDecodeError:
            # Fallback evaluation
            return {
                "score": 5,
                "feedback": "Answer evaluation could not be completed automatically. Manual review recommended.",
                "strengths": ["Response provided"],
                "improvements": ["Consider providing more detailed examples"],
                "keywords_demonstrated": []
            }

    def generate_interview_questions(self, candidate_profile: Dict[str, Any],
                                   job_requirements: Dict[str, Any],
                                   question_count: int = 5) -> List[Dict[str, Any]]:
        """Generate personalized interview questions"""

        prompt = f"""
Generate {question_count} personalized interview questions for this candidate.

CANDIDATE PROFILE:
{json.dumps(candidate_profile, indent=2)}

JOB REQUIREMENTS:
{json.dumps(job_requirements, indent=2)}

Create a mix of technical and behavioral questions. For each question, provide:
- question: the question text
- type: "technical" or "behavioral"
- difficulty: "easy", "medium", or "hard"
- rationale: why this question is relevant

Format as JSON array.
"""

        response = self.generate_response(prompt, max_tokens=1500, temperature=0.7)

        try:
            questions = json.loads(response)
            if isinstance(questions, list):
                return questions
            else:
                return []
        except:
            return []

    def analyze_resume_match(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        """Analyze how well a resume matches a job description"""

        prompt = f"""
Analyze how well this resume matches the job description.

RESUME:
{resume_text[:2000]}...

JOB DESCRIPTION:
{job_description[:2000]}...

Provide analysis as JSON with:
- match_score: 1-10
- key_matches: array of matching skills/experience
- gaps: array of missing requirements
- summary: brief assessment
"""

        response = self.generate_response(prompt, max_tokens=1000, temperature=0.3)

        try:
            analysis = json.loads(response)
            return analysis
        except:
            return {
                "match_score": 5,
                "key_matches": [],
                "gaps": ["Analysis could not be completed"],
                "summary": "Manual review recommended"
            }

    def generate_feedback(self, evaluation_data: Dict[str, Any]) -> str:
        """Generate personalized feedback for the candidate"""

        prompt = f"""
Based on this interview evaluation, generate constructive feedback for the candidate.

EVALUATION DATA:
{json.dumps(evaluation_data, indent=2)}

Write a professional, encouraging feedback message that:
1. Summarizes their performance
2. Highlights strengths
3. Suggests areas for improvement
4. Ends positively

Keep it concise but comprehensive.
"""

        return self.generate_response(prompt, max_tokens=800, temperature=0.6)

    def chat_response(self, message: str, context: Optional[List[Dict[str, Any]]] = None) -> str:
        """Generate a response for the recruiter AI assistant"""

        context_str = ""
        if context:
            context_str = f"\n\nCONVERSATION CONTEXT:\n{json.dumps(context[-5:], indent=2)}"  # Last 5 messages

        prompt = f"""
You are an AI recruitment assistant helping recruiters with hiring decisions.

USER MESSAGE: {message}{context_str}

Provide a helpful, professional response. Be concise but informative.
If asked about candidates, reference their evaluation scores and key strengths.
"""

        return self.generate_response(prompt, max_tokens=600, temperature=0.7)
