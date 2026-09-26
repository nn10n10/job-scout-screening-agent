from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Evaluation(BaseModel):
    verdict: Literal["KEEP", "MAYBE", "SKIP"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    reasons: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    inhouse: bool | None = None
    ses: bool | None = None
    client_site: bool | None = None
    aws: bool | None = None
    terraform: bool | None = None
    cicd: bool | None = None
    docker: bool | None = None
    ecs: bool | None = None
    kubernetes: bool | None = None
    kubernetes_required: bool | None = None
    remote: bool | None = None
    hybrid: bool | None = None
    japanese_primary: bool | None = None
    english_primary: bool | None = None
    oncall: bool | None = None
    salary_min_jpy: int | None = Field(default=None, ge=0)
    casual_interview_required: bool | None = None


@dataclass(frozen=True)
class EvaluationMetadata:
    provider: str
    model_name: str
    evaluated_at: datetime
