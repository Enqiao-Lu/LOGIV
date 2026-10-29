from dataclasses import dataclass

from ..planning import PlanKey, Planner, Validator, plan_key
from .graph import TaskGraph, build_graph
from .symbolic import State, regress
from .verification import Failure, FailureKind


def locate(failure: Failure, graph: TaskGraph) -> tuple[set[int], set[int]]:
    roots = {failure.node} & set(graph.unfinished)
    if failure.edge is not None and failure.edge.condition is not None:
        roots.update(
            edge.target
            for edge in graph.edges
            if edge.source == failure.edge.source
            and edge.condition == failure.edge.condition
            and edge.target in graph.unfinished
        )
    affected = graph.causal_successors(roots)
    replaced = set()
    if (
        failure.kind == FailureKind.EFFECT
        and failure.own_effect
        and failure.node in graph.unfinished
    ):
        replaced.add(failure.node)
    return affected, replaced


@dataclass
class Repairer:
    planner: Planner
    validator: Validator
    rounds: int = 3

    def __post_init__(self):
        if not 1 <= self.rounds <= 3:
            raise ValueError(self.rounds)

    def repair(self, state: State, failure: Failure, graph: TaskGraph) -> TaskGraph | None:
        affected, replaced = locate(failure, graph)
        history: set[PlanKey] = set()
        for _ in range(self.rounds):
            retained = [index for index in graph.unfinished if index not in replaced]
            keep = tuple(graph.plan[index] for index in retained)
            goal = regress(graph.goal, keep)
            if goal is None:
                return None
            fragment = self.planner.plan(state, goal)
            expand = None
            if fragment is not None:
                key = plan_key(fragment)
                if key in history:
                    return None
                history.add(key)
                combined = fragment + keep
                validation = self.validator.validate(state, combined, graph.goal)
                if validation.valid:
                    return build_graph(state, combined, graph.goal)
                if validation.failed_index is not None:
                    if validation.failed_index < len(fragment):
                        continue
                    retained_index = validation.failed_index - len(fragment)
                    if retained_index < len(retained):
                        expand = retained[retained_index]
            if expand is None:
                expand = next(
                    (index for index in retained if index in affected),
                    retained[0] if retained else None,
                )
            if expand is None:
                return None
            replaced.update(graph.causal_successors({expand}))
            history.clear()
        return None
