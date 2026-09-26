from __future__ import annotations

from google import genai
from google.genai import types

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from .base import BaseClassifier, validate_explanations


class GeminiClassifier(BaseClassifier):
    provider = "gemini"

    def __init__(self, api_key: str, model: str, rules: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.model_name = model
        self.rules = rules

    def classify(self, scout: Scout) -> Evaluation:
        response = self.client.models.generate_content(
            model=self.model,
            contents="仅根据以下 Scout/JD 简洁评价；summary、reasons、concerns 的说明文字必须使用简体中文；公司名、职位名和技术术语保留原文。无证据的字段填 null。\n"
                     + scout.model_dump_json(exclude={"scraped_at", "id", "url"}, exclude_none=True),
            config=types.GenerateContentConfig(
                system_instruction=self.rules,
                response_mime_type="application/json",
                response_schema=Evaluation,
                temperature=0,
            ),
        )
        if response.parsed is not None:
            return validate_explanations(Evaluation.model_validate(response.parsed))
        if response.text:
            return validate_explanations(Evaluation.model_validate_json(response.text))
        raise ValueError("Gemini returned no evaluation content")
