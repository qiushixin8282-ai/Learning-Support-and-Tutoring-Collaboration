import argparse
import json
from pathlib import Path

from learning_support_collab.core.models import SupportRequest
from learning_support_collab.core.models import SupportTask
from learning_support_collab.core.lifecycle import transition
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


def run_lifecycle_demo() -> dict:
    """Produce an auditable happy-path handoff for a training demonstration."""
    task = SupportTask(task_id="DEMO-TASK-001", owner="课程讲师")
    transition(task, "assigned", actor="学习服务主任", reason="控制项核验通过")
    transition(task, "drafted", actor="课程讲师", reason="完成辅导材料初稿")
    transition(task, "under_review", actor="课程运营员", reason="提交版本复核")
    transition(task, "published", actor="课程运营员", reason="复核通过")
    transition(task, "handed_off", actor="平台工程师", reason="完成版本交接")
    return task.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="Learning support collaboration demo")
    parser.add_argument("--demo", action="store_true", help="evaluate anonymous synthetic requests")
    parser.add_argument("--lifecycle-demo", action="store_true", help="run a reviewed and auditable task handoff")
    args = parser.parse_args()
    if args.demo:
        print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
        return
    if args.lifecycle_demo:
        print(json.dumps(run_lifecycle_demo(), ensure_ascii=False, indent=2))
        return
    if not args.demo:
        parser.print_help()


if __name__ == "__main__":
    main()
