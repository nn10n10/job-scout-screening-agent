from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from scout_agent.models.evaluation import Evaluation, EvaluationMetadata
from scout_agent.models.scout import Scout


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def generate_report(
    output_dir: Path,
    results: list[tuple[Scout, Evaluation] | tuple[Scout, Evaluation, EvaluationMetadata]],
    *,
    generated_at: datetime | None = None,
    model_name: str | None = None,
) -> tuple[Path, Path]:
    generated_at = generated_at or datetime.now()
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = [
        (item[0], item[1], item[2] if len(item) == 3 else None)
        for item in results
    ]
    ordered = sorted(normalized, key=lambda item: {"KEEP": 0, "MAYBE": 1, "SKIP": 2}[item[1].verdict])
    counts = {verdict: sum(item[1].verdict == verdict for item in ordered) for verdict in ("KEEP", "MAYBE", "SKIP")}
    models = sorted({meta.model_name for _, _, meta in ordered if meta is not None})
    evaluation_model = ", ".join(models) if models else (model_name or "unknown")
    name = generated_at.strftime("%Y-%m-%d_%H%M_report")
    html_path = output_dir / f"{name}.html"
    json_path = output_dir / f"{name}.json"
    suffix = 2
    while html_path.exists() or json_path.exists():
        html_path = output_dir / f"{name}_{suffix}.html"
        json_path = output_dir / f"{name}_{suffix}.json"
        suffix += 1
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("report.html.j2").render(
        generated_at=generated_at, counts=counts, results=ordered,
        evaluation_model=evaluation_model,
        safe_url=_safe_url,
    )
    html_path.write_text(html, encoding="utf-8")
    data = {
        "generated_at": generated_at.isoformat(),
        "new_scouts": len(ordered),
        "evaluation_model": evaluation_model,
        "counts": counts,
        "results": [
            {
                "scout": scout.model_dump(mode="json"),
                "evaluation": {
                    **evaluation.model_dump(mode="json"),
                    "provider": meta.provider if meta else "unknown",
                    "model_name": meta.model_name if meta else "unknown",
                    "evaluated_at": meta.evaluated_at.isoformat() if meta else None,
                },
            }
            for scout, evaluation, meta in ordered
        ],
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path, json_path
