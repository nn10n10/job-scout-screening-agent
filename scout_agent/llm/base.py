from abc import ABC, abstractmethod
import re

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout


MISSING_MESSAGE_CONCERN = "Scout 正文不可用；本次主要依据职位描述评价。"


def validate_explanations(evaluation: Evaluation) -> Evaluation:
    """Reject plainly non-Chinese explanatory prose before it reaches storage."""
    for field, values in (
        ("summary", [evaluation.summary]),
        ("reasons", evaluation.reasons),
        ("concerns", evaluation.concerns),
    ):
        for value in values:
            if not value.strip() or not re.search(r"[\u4e00-\u9fff]", value):
                raise ValueError(
                    f"Evaluation {field} must be written in Simplified Chinese; "
                    "company names, job titles, and technical terms may remain in their original language"
                )
    return evaluation


class BaseClassifier(ABC):
    @abstractmethod
    def classify(self, scout: Scout) -> Evaluation: ...

    def classify_many(self, scouts: list[tuple[str, Scout]]) -> dict[str, Evaluation]:
        return {scout_id: self.classify(scout) for scout_id, scout in scouts}
