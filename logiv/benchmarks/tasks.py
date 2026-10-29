import json
from dataclasses import dataclass
from importlib.resources import as_file, files

from ..planning import Task, load_task
from .runtime import TASKS


@dataclass(frozen=True)
class TaskBundle:
    task: Task
    predicate_descriptions: dict[str, str]
    object_descriptions: dict[str, str]
    static_predicates: frozenset[str]


def load_task_bundle(benchmark: str, task_name: str) -> TaskBundle:
    if benchmark not in TASKS or task_name not in TASKS[benchmark]:
        raise ValueError((benchmark, task_name))
    root = files("logiv")
    config = json.loads(
        root.joinpath("configs", benchmark, f"{task_name}.json").read_text(encoding="utf-8")
    )
    prompts = json.loads(
        root.joinpath("prompts", benchmark, f"{task_name}.json").read_text(encoding="utf-8")
    )
    folder = root.joinpath("pddl", benchmark, task_name)
    with (
        as_file(folder.joinpath("domain.pddl")) as domain,
        as_file(folder.joinpath("problem.pddl")) as problem,
    ):
        task = load_task(domain, problem, prompts["instructions"], config["resources"])
    static_predicates = frozenset(config["static_predicates"])
    if not static_predicates <= task.static_predicates:
        raise ValueError(static_predicates - task.static_predicates)
    if set(prompts["predicates"]) != set(task.predicates):
        raise ValueError(set(prompts["predicates"]) ^ set(task.predicates))
    if set(prompts["objects"]) != set(task.objects) | set(task.constants):
        raise ValueError(set(prompts["objects"]) ^ (set(task.objects) | set(task.constants)))
    return TaskBundle(task, prompts["predicates"], prompts["objects"], static_predicates)
