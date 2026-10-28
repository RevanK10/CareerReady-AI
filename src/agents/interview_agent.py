from config.settings import ANALYTICAL_TEMP, CREATIVE_TEMP
from src.services.llm_client import LLMClient
from templates.prompts import FEEDBACK_PROMPT

class MockInterviewAgent:
    def __init__(self, role_title: str, role_context: str):
        self.role_title = role_title
        self.role_context = role_context
        self.client = LLMClient()
        self.conversation_history = []
        
        self.system_prompt = (
            f"You are an expert executive hiring manager conducting a spoken, live mock interview "
            f"for the role of {self.role_title}.\n"
            f"Role Context: {self.role_context}\n\n"
            f"STRICT RULES FOR VOICE DIALOGUE:\n"
            f"1. Keep responses under 3 sentences. Write conversationally, as it will be spoken out loud.\n"
            f"2. Never use emojis, bullet points, asterisks, or Markdown formatting.\n"
            f"3. Acknowledge the candidate's last answer in one sentence, then ask the next question immediately."
        )

    def _messages(self, extra: str = "") -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += self.conversation_history
        if extra:
            messages.append({"role": "user", "content": extra})
        return messages

    def start_interview(self) -> str:
        response = self.client.chat(
            self._messages("Begin. Welcome me warmly in 2 sentences and ask your first question."),
            temperature=CREATIVE_TEMP,
        )
        self.conversation_history.append({"role": "assistant", "content": response})
        return response

    def reply(self, candidate_transcript: str) -> str:
        self.conversation_history.append({"role": "user", "content": candidate_transcript})
        try:
            response = self.client.chat(self._messages(), temperature=CREATIVE_TEMP)
        except Exception:
            self.conversation_history.pop()  # don't keep an unanswered turn
            raise
        self.conversation_history.append({"role": "assistant", "content": response})
        return response

    @property
    def answers_given(self) -> int:
        return sum(1 for m in self.conversation_history if m["role"] == "user")

    def get_feedback(self) -> str:
        """Coaching report on the whole conversation so far."""
        if not self.answers_given:
            raise RuntimeError("Answer at least one question first, then ask for feedback.")
        label = {"assistant": "Interviewer", "user": "Candidate"}
        transcript = "\n\n".join(f"{label[m['role']]}: {m['content']}" for m in self.conversation_history)
        return self.client.generate(
            FEEDBACK_PROMPT.format(role=self.role_title, transcript=transcript),
            temperature=ANALYTICAL_TEMP,
        )
