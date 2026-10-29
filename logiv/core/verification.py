from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .graph import Edge, TaskGraph
from .symbolic import Atom, Literal, Operator, State


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


class FailureKind(str, Enum):
    PRECONDITION = "PRECONDITION_FAILURE"
    EFFECT = "EFFECT_FAILURE"
    GOAL = "GOAL_FAILURE"


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    node: int
    condition: Literal | None = None
    edge: Edge | None = None
    own_effect: bool = False


@dataclass(frozen=True)
class Observation:
    images: tuple[str, ...] = ()
    robot: Mapping[str, Any] = field(default_factory=dict)
    feedback: Mapping[Atom, bool | None] = field(default_factory=dict)
    raw: Any = None


class Runtime(Protocol):
    def observe(self, active: bool = False) -> Observation: ...

    def execute(self, action: Operator, observation: Observation) -> Observation: ...


class Perception(Protocol):
    def assess(
        self, observation: Observation, atoms: tuple[Atom, ...]
    ) -> Mapping[Atom, bool | None]: ...


@dataclass(frozen=True)
class Check:
    status: Status
    state: State
    failure: Failure | None = None


@dataclass
class Verifier:
    runtime: Runtime
    perception: Perception
    additional_observations: int = 3
    tracked_atoms: frozenset[Atom] = frozenset()
    static_predicates: frozenset[str] = frozenset()

    def __post_init__(self):
        if not 0 <= self.additional_observations <= 3:
            raise ValueError(self.additional_observations)

    def state_gate(
        self,
        state: State,
        required: frozenset[Literal],
        observation: Observation,
    ) -> tuple[State, Observation, bool]:
        atoms = tuple(
            sorted(
                (self.tracked_atoms | {literal.atom for literal in required})
                - {
                    literal.atom
                    for literal in required
                    if literal.atom[0] == "=" or literal.atom[0] in self.static_predicates
                }
            )
        )
        for attempt in range(self.additional_observations + 1):
            if attempt:
                observation = self.runtime.observe(active=True)
            visual = self.perception.assess(observation, atoms)
            evidence = {}
            for atom in atoms:
                value = visual.get(atom)
                sensor = observation.feedback.get(atom)
                if value is not None and type(value) is not bool:
                    value = None
                if sensor is not None and type(sensor) is not bool:
                    sensor = None
                evidence[atom] = sensor if value is None else value
                if sensor is not None and value is not None and sensor != value:
                    evidence[atom] = None
            state = state.update(evidence)
            if all(state.truth(literal) is not None for literal in required) and not state.unknown:
                return state, observation, True
        return state, observation, False

    def verify_and_execute(self, node: int, graph: TaskGraph, state: State) -> Check:
        observation = self.runtime.observe()
        if node == graph.goal_node:
            state, _, resolved = self.state_gate(state, graph.goal, observation)
            if not resolved:
                return Check(Status.UNRESOLVED, state)
            missing = next(
                (literal for literal in sorted(graph.goal) if not state.holds((literal,))), None
            )
            if missing is not None:
                edge = next(
                    (
                        edge
                        for edge in graph.edges
                        if edge.target == node and edge.condition == missing
                    ),
                    None,
                )
                return Check(Status.FAIL, state, Failure(FailureKind.GOAL, node, missing, edge))
            return Check(Status.PASS, state)
        action = graph.plan[node]
        incoming = tuple(
            edge for edge in graph.edges if edge.target == node and edge.condition is not None
        )
        required = action.preconditions | frozenset(edge.condition for edge in incoming)
        state, observation, resolved = self.state_gate(state, required, observation)
        if not resolved:
            return Check(Status.UNRESOLVED, state)
        for literal in sorted(action.preconditions):
            if not state.holds((literal,)):
                edge = next((edge for edge in incoming if edge.condition == literal), None)
                return Check(
                    Status.FAIL, state, Failure(FailureKind.PRECONDITION, node, literal, edge)
                )
        if not graph.predecessors_complete(node):
            edge = next(
                edge
                for edge in graph.edges
                if edge.target == node and edge.source not in graph.completed
            )
            return Check(
                Status.FAIL, state, Failure(FailureKind.PRECONDITION, node, edge.condition, edge)
            )
        observation = self.runtime.execute(action, observation)
        support = graph.live_support(graph.completed | {node})
        required = action.effects | frozenset(edge.condition for edge in support)
        state, _, resolved = self.state_gate(state, required, observation)
        if state.holds(action.effects):
            graph.completed.add(node)
        if not resolved:
            return Check(Status.UNRESOLVED, state)
        for literal in sorted(action.effects):
            if not state.holds((literal,)):
                return Check(
                    Status.FAIL, state, Failure(FailureKind.EFFECT, node, literal, own_effect=True)
                )
        for edge in support:
            if not state.holds((edge.condition,)):
                return Check(
                    Status.FAIL, state, Failure(FailureKind.EFFECT, node, edge.condition, edge)
                )
        return Check(Status.PASS, state)
