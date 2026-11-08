"""只读汇总准确制作根任务及后继的全周期用量、费用、失败指纹和未完成阶段。"""

import argparse
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.article_regression_repository import ObservationRepository
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.runtime.production_lifecycle_cost import lifecycle_task_ids, summarize_lifecycle_cost
from agent4pharma.runtime.workflow_observation import read_observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with production_unit(args.root, read_only=True) as work:
        tasks = work.tasks.list()
    ids = lifecycle_task_ids(tasks, args.task)
    events = {
        identity: list(read_observations(ObservationRepository(args.root), [identity], ()))
        for identity in ids
    }
    result = summarize_lifecycle_cost(tasks, events, args.task).model_dump_json(indent=2)
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result)


if __name__ == "__main__":
    main()
