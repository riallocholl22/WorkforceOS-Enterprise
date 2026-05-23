import json
import re
from typing import List, Dict, Any, Optional

try:
    from backend.services.openai_service import OpenAIService
    openai_available = True
except ImportError:
    openai_available = False


class InterviewService:
    """AI-powered interview service with GPT evaluation and explainable scoring"""

    def __init__(self):
        try:
            self.openai_service = OpenAIService() if openai_available else None
        except Exception:
            self.openai_service = None

        # Predefined question templates
        self.question_templates = {
            "technical": {
                "junior": [
                    "Can you explain how {technology} works?",
                    "What is the difference between {concept1} and {concept2}?",
                    "How would you debug a {problem_type} issue?",
                    "Can you walk me through your approach to {task}?"
                ],
                "mid": [
                    "How would you design a system to handle {requirement}?",
                    "What are the trade-offs of using {technology1} vs {technology2}?",
                    "How do you ensure code quality in a team environment?",
                    "Describe a challenging technical problem you solved."
                ],
                "senior": [
                    "How would you architect a solution for {complex_requirement}?",
                    "What metrics would you use to evaluate {system_component} performance?",
                    "How do you mentor junior developers on {advanced_topic}?",
                    "Describe your approach to technical leadership."
                ]
            },
            "behavioral": [
                "Tell me about a time when you faced a difficult challenge at work.",
                "How do you handle conflicting priorities or deadlines?",
                "Describe a situation where you had to work with a difficult team member.",
                "How do you approach learning new technologies or skills?",
                "Tell me about a project you led and what you learned from it."
            ]
        }
        self.live_keywords = [
            "architecture", "api", "database", "performance", "testing", "deployment",
            "scalability", "security", "team", "tradeoff", "debug", "impact", "design",
            "ownership", "stakeholder", "monitoring", "automation", "docker", "fastapi"
        ]

    def _fill_template(self, template: str, skill: str) -> str:
        replacements = {
            "technology": skill,
            "task": f"working with {skill}",
            "concept1": skill,
            "concept2": "alternative approach",
            "problem_type": f"{skill} production",
            "requirement": f"{skill} scalability",
            "technology1": skill,
            "technology2": "a competing solution",
            "complex_requirement": f"an enterprise-grade {skill} platform",
            "system_component": f"{skill} service",
            "advanced_topic": skill,
        }
        try:
            return template.format(**replacements)
        except KeyError:
            return template.replace("{technology}", skill)

    def generate_questions(self, candidate_id: str, job_description: str = "",
                          candidate_skills: List[str] = [],
                          experience_level: str = "mid",
                          interview_type: str = "technical") -> List[Dict[str, Any]]:
        """Generate personalized interview questions"""

        questions = []

        if interview_type == "technical" or interview_type == "mixed":
            # Generate technical questions based on skills and experience
            tech_questions = self._generate_technical_questions(
                candidate_skills, experience_level, job_description
            )
            questions.extend(tech_questions)

        if interview_type == "behavioral" or interview_type == "mixed":
            # Add behavioral questions
            behavioral_questions = self._generate_behavioral_questions()
            questions.extend(behavioral_questions)

        # Limit to 5-8 questions total
        questions = questions[:8]

        # Add metadata to each question
        for i, q in enumerate(questions):
            q.update({
                "id": f"q_{i+1}",
                "order": i + 1,
                "type": q.get("type", "technical"),
                "difficulty": q.get("difficulty", "medium"),
                "estimated_time": 180,  # 3 minutes
                "scoring_criteria": self._get_scoring_criteria(q["type"])
            })

        return questions

    def _generate_technical_questions(self, skills: List[str], level: str,
                                    job_description: str) -> List[Dict[str, Any]]:
        """Generate technical questions based on candidate skills"""

        questions = []
        templates = self.question_templates["technical"].get(level, self.question_templates["technical"]["mid"])

        # Use candidate skills to personalize questions
        if skills:
            for skill in skills[:3]:  # Use top 3 skills
                template = templates[len(questions) % len(templates)]
                question = self._fill_template(template, skill)
                questions.append({
                    "question": question,
                    "type": "technical",
                    "skill_focus": skill,
                    "difficulty": level
                })

        # Add job-specific questions if description provided
        if job_description:
            job_questions = self._extract_job_requirements(job_description)
            for req in job_questions[:2]:  # Add 2 job-specific questions
                question = f"How would you implement {req} in a production environment?"
                questions.append({
                    "question": question,
                    "type": "technical",
                    "job_requirement": req,
                    "difficulty": "medium"
                })

        return questions

    def _generate_behavioral_questions(self) -> List[Dict[str, Any]]:
        """Generate behavioral interview questions"""

        questions = []
        behavioral_templates = self.question_templates["behavioral"]

        # Select 2-3 behavioral questions
        selected = behavioral_templates[:3]

        for question in selected:
            questions.append({
                "question": question,
                "type": "behavioral",
                "difficulty": "medium"
            })

        return questions

    def _extract_job_requirements(self, job_description: str) -> List[str]:
        """Extract key requirements from job description"""

        # Simple keyword extraction (in production, use NLP)
        tech_keywords = [
            "API", "database", "frontend", "backend", "cloud", "security",
            "testing", "deployment", "monitoring", "scalability", "performance"
        ]

        requirements = []
        desc_lower = job_description.lower()

        for keyword in tech_keywords:
            if keyword in desc_lower:
                requirements.append(keyword)

        return requirements[:3]  # Return top 3 requirements

    def _get_scoring_criteria(self, question_type: str) -> Dict[str, Any]:
        """Get scoring criteria for different question types"""

        criteria = {
            "technical": {
                "accuracy": 0.3,
                "completeness": 0.25,
                "approach": 0.25,
                "communication": 0.2
            },
            "behavioral": {
                "relevance": 0.3,
                "specificity": 0.25,
                "reflection": 0.25,
                "communication": 0.2
            }
        }

        return criteria.get(question_type, criteria["technical"])

    def evaluate_answer(self, question: str, answer: str, time_taken: float) -> Dict[str, Any]:
        """Evaluate individual answer using GPT or fallback scoring"""

        if not answer or not answer.strip():
            return {
                "score": 0,
                "feedback": "No answer provided",
                "strengths": [],
                "improvements": ["Please provide a complete answer"],
                "time_efficiency": "N/A"
            }

        # Use GPT evaluation if available
        if self.openai_service:
            try:
                return self._gpt_evaluate_answer(question, answer, time_taken)
            except Exception as e:
                print(f"GPT evaluation failed: {e}")
                # Fall back to basic scoring

        # Basic scoring fallback
        return self._basic_evaluate_answer(question, answer, time_taken)

    def evaluate_live_answer(self, question: str, answer: str) -> Dict[str, Any]:
        """Lightweight live scoring for transcript chunks."""
        cleaned = (answer or "").strip().lower()
        if not cleaned:
            return {
                "live_score": 0,
                "keywords_detected": [],
                "confidence_level": "low",
                "good_signals": [],
                "risk_signals": ["No response detected yet"],
                "follow_up_suggestions": [
                    "Start by restating the problem in your own words, then outline your approach step-by-step."
                ],
            }

        words = cleaned.split()
        keyword_hits = [keyword for keyword in self.live_keywords if keyword in cleaned][:6]
        reasoning_hits = [token for token in ["because", "example", "impact", "tradeoff", "result", "measured"] if token in cleaned]
        structure_hits = [token for token in ["first", "second", "then", "finally", "step", "approach", "assumption"] if token in cleaned]
        metric_hits = bool(re.search(r"\b\d+(\.\d+)?%?\b", cleaned))

        # Stage detection (helps the UI feel less "fake" early in an answer)
        if len(words) < 12:
            stage = "starting"
        elif len(words) < 35:
            stage = "developing"
        else:
            stage = "substantial"

        live_score = 35
        live_score += min(25, len(words) // 3)
        live_score += min(25, len(keyword_hits) * 5)
        live_score += min(15, len(reasoning_hits) * 4)
        live_score += min(10, len(structure_hits) * 2)
        if metric_hits:
            live_score += 6
        live_score = max(0, min(100, int(live_score)))

        if live_score >= 75:
            confidence_level = "high"
        elif live_score >= 50:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        good_signals = []
        if keyword_hits:
            good_signals.append("Relevant technical or behavioral keywords detected")
        if reasoning_hits:
            good_signals.append("Answer includes explanation or evidence")
        if structure_hits:
            good_signals.append("Answer has a clear structure (steps/approach)")
        if metric_hits:
            good_signals.append("Specific detail or measurable impact detected")

        risk_signals = []
        if len(words) < 12:
            risk_signals.append("Response is still brief")
        if not keyword_hits:
            risk_signals.append("Few role-specific keywords detected")
        if not reasoning_hits and len(words) >= 18:
            risk_signals.append("Add reasoning or an example to strengthen the answer")

        followups: list[str] = []
        if stage == "starting":
            followups.append("Give a quick high-level approach first, then add details.")
        if not reasoning_hits:
            followups.append("Add one concrete example or failure mode to support your approach.")
        if not metric_hits:
            followups.append("Mention at least one metric or signal you would monitor in production.")

        return {
            "live_score": live_score,
            "keywords_detected": keyword_hits,
            "confidence_level": confidence_level,
            "good_signals": good_signals,
            "risk_signals": risk_signals,
            "answer_stage": stage,
            "follow_up_suggestions": followups[:3],
        }

    def generate_follow_up_question(self, question: str, answer: str, score: float, question_type: str = "technical") -> Optional[Dict[str, Any]]:
        """Insert a follow-up when the previous answer needs clarification or deeper probing."""
        if score <= 4:
            return {
                "question": f"You touched on part of this. Can you give a more concrete example related to: {question}",
                "type": question_type,
                "difficulty": "medium",
                "follow_up": True,
            }

        if score >= 8:
            return {
                "question": f"Good start. Now extend that answer to a real-world scenario with tradeoffs and risks for: {question}",
                "type": "scenario-based",
                "difficulty": "hard",
                "follow_up": True,
            }

        return None

    def build_final_recommendation(self, breakdown: Dict[str, Any]) -> str:
        overall = breakdown.get("overall", 0)
        if overall >= 8:
            return "hire"
        if overall >= 6:
            return "consider"
        return "reject"

    def _gpt_evaluate_answer(self, question: str, answer: str, time_taken: float) -> Dict[str, Any]:
        """Use GPT to evaluate the answer"""

        prompt = f"""
You are an expert technical interviewer evaluating a candidate's response.

QUESTION: {question}

CANDIDATE ANSWER: {answer}

TIME TAKEN: {time_taken:.1f} seconds

Please evaluate this answer on a scale of 1-10 (10 being excellent) and provide:
1. Overall score (1-10)
2. Brief feedback (2-3 sentences)
3. Key strengths (2-3 bullet points)
4. Areas for improvement (2-3 bullet points)
5. Time efficiency assessment

Format your response as JSON:
{{
    "score": <number>,
    "feedback": "<string>",
    "strengths": ["<string>", "<string>"],
    "improvements": ["<string>", "<string>"],
    "time_efficiency": "<assessment>"
}}
"""

        response = self.openai_service.generate_response(prompt)

        try:
            # Parse JSON response
            evaluation = json.loads(response)
            return evaluation
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return self._basic_evaluate_answer(question, answer, time_taken)

    def _basic_evaluate_answer(self, question: str, answer: str, time_taken: float) -> Dict[str, Any]:
        """
        Recruiter-grade rule-based answer evaluation.
        Deterministic and model-free, but tries to be high-signal and human-readable.
        """

        q = (question or "").strip()
        a = (answer or "").strip()
        ql = q.lower()
        al = a.lower()

        word_count = len(a.split())
        has_numbers = bool(re.search(r"\b\d+(\.\d+)?%?\b", a))
        has_structure = any(token in al for token in ["first", "second", "then", "finally", "step", "1)", "2)", "3)"])
        has_reasoning = any(token in al for token in ["because", "therefore", "however", "trade-off", "tradeoff", "for example", "example", "impact"])
        hesitant = any(token in al for token in ["i think", "i believe", "maybe", "probably"])

        # Relevance (keyword overlap, stopword-light)
        q_tokens = {t for t in re.findall(r"[a-zA-Z0-9']+", ql) if len(t) > 3}
        a_tokens = {t for t in re.findall(r"[a-zA-Z0-9']+", al) if len(t) > 3}
        overlap = len(q_tokens & a_tokens)
        relevance = 0.0 if not q_tokens else min(1.0, overlap / max(4.0, len(q_tokens) * 0.55))

        # Depth (signals, not guarantees)
        depth = 0.0
        if word_count >= 35:
            depth += 0.25
        if has_reasoning:
            depth += 0.30
        if has_numbers:
            depth += 0.20
        if any(t in al for t in ["monitoring", "logs", "metrics", "rollback", "testing", "edge case", "failure mode"]):
            depth += 0.25
        depth = min(1.0, depth)

        # Clarity
        clarity = 0.35 + (0.35 if has_structure else 0.0) + (0.20 if word_count >= 18 else 0.0) + (0.10 if not hesitant else 0.0)
        clarity = max(0.0, min(1.0, clarity))

        # Time efficiency
        time_efficiency = "Good"
        time_penalty = 0.0
        if time_taken and time_taken < 25:
            time_efficiency = "Too fast - may indicate surface-level response"
            time_penalty = 0.10
        elif time_taken and time_taken > 240:
            time_efficiency = "Slow - may indicate uncertainty or lack of structure"
            time_penalty = 0.10

        # Overall score 1..10
        composite = (0.42 * relevance + 0.36 * depth + 0.22 * clarity) - time_penalty
        # Map to 1..10 with a gentle baseline.
        score = int(round(max(1.0, min(10.0, 2.5 + composite * 7.5))))

        strengths: List[str] = []
        improvements: List[str] = []

        if relevance >= 0.55:
            strengths.append("Stayed on-topic and addressed the core question")
        else:
            improvements.append("Tie your answer more directly back to the question prompt")

        if depth >= 0.55:
            strengths.append("Good depth, with trade-offs and practical considerations")
        else:
            improvements.append("Add 1-2 concrete examples, trade-offs, or edge cases to show depth")

        if clarity >= 0.65:
            strengths.append("Clear structure and communication")
        else:
            improvements.append("Use a clearer structure (steps, assumptions, and outcome) to improve clarity")

        if has_numbers:
            strengths.append("Included measurable impact or specific details")
        else:
            improvements.append("Include one measurable outcome or specific signal (metric, latency, error rate, time saved)")

        # Follow-ups (makes the AI interviewer feel real)
        followups: List[str] = [
            "What trade-offs did you consider, and what would make you choose a different approach?",
            "How would you validate this in production (monitoring, logs, rollout, failure modes)?",
        ]

        # Recruiter-readable feedback
        if score >= 8:
            feedback = "Strong answer. The candidate communicates clearly and covers practical trade-offs. Recommend moving to deeper probing on edge cases and production constraints."
        elif score >= 6:
            feedback = "Solid baseline answer with some relevant signals. Recommend follow-up questions to confirm depth (trade-offs, failure modes, metrics)."
        elif score >= 4:
            feedback = "Partial answer. The candidate may understand the topic, but the response needs clearer structure and more concrete examples."
        else:
            feedback = "Weak answer. Recommend re-asking with a smaller scope and checking fundamentals before advancing."

        return {
            "score": score,
            "feedback": feedback,
            "strengths": strengths[:3],
            "improvements": improvements[:3],
            "time_efficiency": time_efficiency,
            "signals": {
                "relevance": round(relevance, 2),
                "depth": round(depth, 2),
                "clarity": round(clarity, 2),
                "word_count": word_count,
                "has_numbers": has_numbers,
            },
            "follow_up_questions": followups[:3],
        }

    def calculate_final_score(self, questions: List[Dict[str, Any]],
                            answers: List[Dict[str, Any]],
                            behavioral_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate comprehensive final interview score"""

        if not answers:
            return {
                "overall_score": 0,
                "breakdown": {},
                "recommendation": "No answers provided",
                "insights": ["Interview was not completed"],
                "question_evaluations": [],
                "behavioral_analysis": {}
            }

        # Calculate average scores
        total_score = 0
        question_evaluations = []

        for answer in answers:
            evaluation = answer.get("evaluation", {})
            score = evaluation.get("score", 5)
            total_score += score

            question_evaluations.append({
                "question_id": answer.get("question_id"),
                "question": answer.get("question_text", "")[:100] + "...",
                "score": score,
                "feedback": evaluation.get("feedback", ""),
                "time_taken": answer.get("time_taken", 0)
            })

        average_score = total_score / len(answers)

        # Behavioral analysis
        behavioral_analysis = self._analyze_behavioral_data(behavioral_data)

        # Technical vs Behavioral breakdown
        technical_scores = []
        behavioral_scores = []

        for answer in answers:
            q_type = None
            for q in questions:
                if q.get("id") == answer.get("question_id"):
                    q_type = q.get("type")
                    break

            if q_type == "technical":
                technical_scores.append(answer.get("evaluation", {}).get("score", 5))
            elif q_type == "behavioral":
                behavioral_scores.append(answer.get("evaluation", {}).get("score", 5))

        breakdown = {
            "overall": round(average_score, 1),
            "technical": round(sum(technical_scores) / len(technical_scores), 1) if technical_scores else 0,
            "behavioral": round(sum(behavioral_scores) / len(behavioral_scores), 1) if behavioral_scores else 0,
            "communication": behavioral_analysis.get("communication_score", 7),
            "problem_solving": behavioral_analysis.get("problem_solving_score", 7)
        }

        # Generate recommendation
        recommendation = self._generate_recommendation(breakdown, behavioral_analysis)

        # Generate insights
        insights = self._generate_insights(breakdown, question_evaluations, behavioral_analysis)

        return {
            "overall_score": round(average_score, 1),
            "breakdown": breakdown,
            "recommendation": recommendation,
            "insights": insights,
            "question_evaluations": question_evaluations,
            "behavioral_analysis": behavioral_analysis
        }

    def _analyze_behavioral_data(self, behavioral_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze behavioral patterns from interview data"""

        analysis = {
            "communication_score": 7,
            "confidence_score": 7,
            "engagement_score": 7,
            "consistency_score": 7,
            "patterns": []
        }

        # Analyze response times
        response_times = behavioral_data.get("response_times", [])
        if response_times:
            avg_time = sum(response_times) / len(response_times)
            if avg_time < 60:  # Too fast
                analysis["communication_score"] -= 1
                analysis["patterns"].append("Very quick responses may indicate lack of deep thinking")
            elif avg_time > 240:  # Too slow
                analysis["confidence_score"] -= 1
                analysis["patterns"].append("Slow responses may indicate uncertainty")

        # Analyze answer lengths
        answer_lengths = behavioral_data.get("answer_lengths", [])
        if answer_lengths:
            avg_length = sum(answer_lengths) / len(answer_lengths)
            if avg_length < 20:  # Too short
                analysis["communication_score"] -= 1
                analysis["patterns"].append("Brief answers may lack depth")
            elif avg_length > 200:  # Too long
                analysis["communication_score"] += 0.5
                analysis["patterns"].append("Detailed answers show thorough thinking")

        return analysis

    def _generate_recommendation(self, breakdown: Dict[str, Any],
                               behavioral_analysis: Dict[str, Any]) -> str:
        """Generate hiring recommendation based on scores"""

        overall = breakdown.get("overall", 0)

        if overall >= 8.5:
            return "Strong Hire - Excellent candidate with outstanding technical and communication skills"
        elif overall >= 7.0:
            return "Hire - Good candidate with solid skills and potential"
        elif overall >= 5.5:
            return "Consider - Adequate performance, may need development support"
        elif overall >= 4.0:
            return "Borderline - Some potential but significant gaps to address"
        else:
            return "Reject - Does not meet minimum requirements at this time"

    def _generate_insights(self, breakdown: Dict[str, Any],
                          question_evaluations: List[Dict[str, Any]],
                          behavioral_analysis: Dict[str, Any]) -> List[str]:
        """Generate key insights from the interview"""

        insights = []

        # Score insights
        overall = breakdown.get("overall", 0)
        if overall >= 8:
            insights.append("Exceptional performance across all evaluation criteria")
        elif overall >= 6:
            insights.append("Solid performance with room for growth in some areas")
        else:
            insights.append("Performance indicates need for additional training or experience")

        # Technical vs behavioral comparison
        technical = breakdown.get("technical", 0)
        behavioral = breakdown.get("behavioral", 0)

        if technical > behavioral + 1:
            insights.append("Stronger in technical skills than behavioral/soft skills")
        elif behavioral > technical + 1:
            insights.append("Stronger in behavioral skills than technical abilities")
        else:
            insights.append("Well-balanced technical and behavioral skills")

        # Time management insights
        fast_responses = sum(1 for q in question_evaluations if q.get("time_taken", 0) < 45)
        if fast_responses > len(question_evaluations) * 0.6:
            insights.append("Quick thinking and decision-making abilities demonstrated")

        # Add behavioral patterns
        patterns = behavioral_analysis.get("patterns", [])
        insights.extend(patterns[:2])  # Add up to 2 behavioral insights

        return insights
