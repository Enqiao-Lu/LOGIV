import math
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from ..core.symbolic import Literal, Operator, State
from .pddl import Task, parse, typed_names

Plan = tuple[Operator, ...]
PlanKey = tuple[tuple[str, ...], ...]


def plan_key(plan: Plan) -> PlanKey:
    return tuple(action.signature for action in plan)


@dataclass(frozen=True)
class Validation:
    valid: bool
    failed_index: int | None = None
    detail: str = ""


class Planner(Protocol):
    def plan(
        self, state: State, goal: frozenset[Literal], excluded: frozenset[PlanKey] = frozenset()
    ) -> Plan | None: ...


class Validator(Protocol):
    def validate(self, state: State, plan: Plan, goal: frozenset[Literal]) -> Validation: ...


def run_process(command: list[str], directory: str, timeout: float) -> tuple[int, str]:
    with subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
    return process.returncode, output


def write_inputs(
    task: Task, state: State, goal: frozenset[Literal], directory: str
) -> tuple[Path, Path]:
    domain = Path(directory, "domain.pddl")
    problem = Path(directory, "problem.pddl")
    domain.write_text(task.domain_text)
    problem.write_text(task.problem(state, goal))
    return domain, problem


def exclude_plans(domain: Path, problem: Path, excluded: frozenset[PlanKey]):
    if not excluded:
        return
    domain_tree, problem_tree = parse(domain.read_text()), parse(problem.read_text())
    objects = next(
        (
            section
            for section in problem_tree
            if isinstance(section, list) and section[0] == ":objects"
        ),
        None,
    )
    if objects is not None:
        constants = next(
            (
                section
                for section in domain_tree
                if isinstance(section, list) and section[0] == ":constants"
            ),
            None,
        )
        if constants is None:
            constants = [":constants"]
            domain_tree.insert(2, constants)
        merged = typed_names(constants[1:]) | typed_names(objects[1:])
        constants[1:] = [token for name, kind in merged.items() for token in (name, "-", kind)]
        objects[1:] = []
    predicates = next(
        section
        for section in domain_tree
        if isinstance(section, list) and section[0] == ":predicates"
    )
    initial = next(
        section for section in problem_tree if isinstance(section, list) and section[0] == ":init"
    )
    goal = next(
        section for section in problem_tree if isinstance(section, list) and section[0] == ":goal"
    )
    goal[1] = ["and", goal[1]]
    requirements = next(
        (
            section
            for section in domain_tree
            if isinstance(section, list) and section[0] == ":requirements"
        ),
        None,
    )
    if requirements is None:
        requirements = [":requirements", ":strips"]
        domain_tree.insert(2, requirements)
    for feature in (":conditional-effects", ":equality", ":negative-preconditions"):
        if feature not in requirements:
            requirements.append(feature)
    actions = [
        section for section in domain_tree if isinstance(section, list) and section[0] == ":action"
    ]
    for number, sequence in enumerate(sorted(excluded)):
        monitors = [[f"logiv-excluded-{number}-{step}"] for step in range(len(sequence) + 1)]
        if any(monitor[0] == predicate[0] for monitor in monitors for predicate in predicates[1:]):
            raise ValueError(monitors)
        predicates.extend(monitors)
        initial.append(monitors[0])
        goal[1].append(["not", monitors[-1]])
        for action in actions:
            parameters = action[action.index(":parameters") + 1]
            variables = [token for token in parameters if token.startswith("?")]
            effect_index = action.index(":effect") + 1
            effects = ["and", action[effect_index]]
            for step, signature in enumerate(sequence):
                condition = monitors[step]
                if action[1] != signature[0]:
                    effects.append(["when", condition, ["not", condition]])
                    continue
                matches = [
                    ["=", variable, value] for variable, value in zip(variables, signature[1:])
                ]
                effects.append(
                    [
                        "when",
                        ["and", condition, *matches],
                        ["and", ["not", condition], monitors[step + 1]],
                    ]
                )
                for match in matches:
                    effects.append(["when", ["and", condition, ["not", match]], ["not", condition]])
            effects.append(["when", monitors[-1], ["not", monitors[-1]]])
            action[effect_index] = effects

    def serialize(tree):
        return (
            "("
            + " ".join(serialize(item) if isinstance(item, list) else item for item in tree)
            + ")"
        )

    domain.write_text(serialize(domain_tree))
    problem.write_text(serialize(problem_tree))


@dataclass
class FastDownward:
    task: Task
    executable: Path
    timeout: float = 30.0

    def plan(
        self, state: State, goal: frozenset[Literal], excluded: frozenset[PlanKey] = frozenset()
    ) -> Plan | None:
        with TemporaryDirectory(prefix="logiv-plan-") as directory:
            domain, problem = write_inputs(self.task, state, goal, directory)
            exclude_plans(domain, problem, excluded)
            output = Path(directory, "sas_plan")
            command = [
                sys.executable,
                str(Path(self.executable).resolve()),
                "--alias",
                "lama-first",
                "--overall-time-limit",
                f"{math.ceil(self.timeout)}s",
                "--plan-file",
                str(output),
                str(domain),
                str(problem),
            ]
            try:
                code, detail = run_process(command, directory, self.timeout)
            except subprocess.TimeoutExpired:
                return None
            if code in {11, 12, 20, 21, 22, 23, 24}:
                return None
            if code != 0 or not output.exists():
                raise RuntimeError(detail)
            plan = tuple(
                self.task.ground(tuple(parse(line)))
                for line in output.read_text().splitlines()
                if line.strip().startswith("(")
            )
            return plan


@dataclass
class VAL:
    task: Task
    executable: str = "Validate"
    timeout: float = 5.0

    def validate(self, state: State, plan: Plan, goal: frozenset[Literal]) -> Validation:
        with TemporaryDirectory(prefix="logiv-val-") as directory:
            domain, problem = write_inputs(self.task, state, goal, directory)
            path = Path(directory, "plan")
            path.write_text(
                "".join(f"{index}: {action.pddl()}\n" for index, action in enumerate(plan))
            )
            try:
                code, detail = run_process(
                    [str(self.executable), "-v", str(domain), str(problem), str(path)],
                    directory,
                    self.timeout,
                )
            except subprocess.TimeoutExpired:
                return Validation(False, detail="TIMEOUT")
        if code == 0 and "Plan valid" in detail:
            return Validation(True)
        current = state
        for index, action in enumerate(plan):
            if not current.holds(action.preconditions):
                return Validation(False, index, detail)
            current = action.apply(current)
        return Validation(False, detail=detail)


def prepare_plan(
    state: State,
    goal: frozenset[Literal],
    planner: Planner,
    validator: Validator,
    attempts: int,
) -> Plan | None:
    excluded: set[PlanKey] = set()
    for _ in range(attempts):
        plan = planner.plan(state, goal, frozenset(excluded))
        if plan is None or plan_key(plan) in excluded:
            return None
        if validator.validate(state, plan, goal).valid:
            return plan
        excluded.add(plan_key(plan))
    return None
