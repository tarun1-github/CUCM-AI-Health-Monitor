"""JSON raw diagnostic report exporter."""

from pathlib import Path

from backend.models.health import HealthSnapshot


def write_json_report(snapshot: HealthSnapshot, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    return output_path
