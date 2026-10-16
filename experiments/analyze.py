import json
from pathlib import Path

import hydra
from hydra.core.hydra_config import HydraConfig

from app.schemas import AnalyzeRequest
from app.services.analyzer import Analyzer
from app.settings import Settings


@hydra.main(version_base="1.3", config_path="../conf", config_name="experiment")
def main(config):
    request = AnalyzeRequest(app_id=str(config.app_id), country=config.country, review_count=config.review_count, seed=config.seed, mode=config.mode)
    settings = Settings.model_validate({**Settings.from_env().model_dump(), "sentiment_threads": config.sentiment_threads})
    classifier = None
    if request.mode == "quality" and settings.quality_enabled:
        from app.services.sentiment import load_classifier

        classifier = load_classifier(settings)
    report = Analyzer(settings, classifier).analyze(request)
    output = Path(HydraConfig.get().runtime.output_dir) / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved experiment to {output}")


if __name__ == "__main__":
    main()
