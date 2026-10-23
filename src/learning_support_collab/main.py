import argparse
import json
from pathlib import Path

from learning_support_collab.core.models import SupportRequest
from learning_support_collab.core.workflow import evaluate_request
from learning_support_collab.utils.io import load_json, load_yaml


ROOT = Path(__file__).resolve().parents[2]


def run_demo() -> list[dict]:
    config = load_yaml(ROOT / "config" / "default.yaml")
    records = load_json(ROOT / "data" / "sample_requests.json")
    return [
        evaluate_request(SupportRequest(**record), required_rule_version=config["required_rule_version"]).to_dict()
        for record in records
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Learning support collaboration demo")
    parser.add_argument("--demo", action="store_true", help="evaluate anonymous synthetic requests")
    args = parser.parse_args()
    if not args.demo:
        parser.print_help()
        return
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
