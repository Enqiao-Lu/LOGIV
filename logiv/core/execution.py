from dataclasses import dataclass
from enum import Enum

from ..planning import Planner, Validator, prepare_plan
from .graph import build_graph
from .repair import Repairer
from .symbolic import Literal, Operator, State
from .verification import Failure, Status, Verifier


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    state: State
    rounds: int
    completed: tuple[Operator, ...]
    failures: tuple[Failure, ...]
    reason: str


@dataclass
class LOGIV:
    planner: Planner
    validator: Validator
    verifier: Verifier
    execution_budget: int
    preparation_attempts: int = 3

    def __post_init__(self):
        if self.execution_budget < 1 or self.preparation_attempts < 1:
            raise ValueError((self.execution_budget, self.preparation_attempts))

    def run(self, initial_state: State, goal: frozenset[Literal]) -> Result:
        state = initial_state
        completed: list[Operator] = []
        failures: list[Failure] = []
        rounds = 0

        def finish(outcome, reason):
            return Result(outcome, state, rounds, tuple(completed), tuple(failures), reason)

        plan = prepare_plan(state, goal, self.planner, self.validator, self.preparation_attempts)
        if plan is None:
            return finish(Outcome.FAILURE, "PLANNING_FAILURE")
        graph = build_graph(state, plan, goal)
        repairer = Repairer(self.planner, self.validator)
        for rounds in range(1, self.execution_budget + 1):
            node = graph.select(state)
            if node is None:
                return finish(Outcome.FAILURE, "NO_READY_NODE")
            check = self.verifier.verify_and_execute(node, graph, state)
            state = check.state
            if node != graph.goal_node and node in graph.completed:
                completed.append(graph.plan[node])
            if check.status == Status.UNRESOLVED:
                return finish(Outcome.FAILURE, "UNRESOLVED")
            if node == graph.goal_node and check.status == Status.PASS:
                return finish(Outcome.SUCCESS, "GOAL_VERIFIED")
            if check.status == Status.FAIL:
                failures.append(check.failure)
                graph = repairer.repair(state, check.failure, graph)
                if graph is None:
                    return finish(Outcome.FAILURE, "REPAIR_FAILURE")
        return finish(Outcome.FAILURE, "EXECUTION_BUDGET")
