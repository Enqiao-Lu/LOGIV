from dataclasses import dataclass, field

from .symbolic import Literal, Operator, State, consistent

START = -1


@dataclass(frozen=True)
class Edge:
    source: int
    target: int
    condition: Literal | None = None


@dataclass
class TaskGraph:
    plan: tuple[Operator, ...]
    goal: frozenset[Literal]
    edges: tuple[Edge, ...]
    completed: set[int] = field(default_factory=lambda: {START})

    @property
    def goal_node(self) -> int:
        return len(self.plan)

    @property
    def unfinished(self) -> tuple[int, ...]:
        return tuple(i for i in range(len(self.plan)) if i not in self.completed)

    def predecessors_complete(self, node: int) -> bool:
        return all(edge.source in self.completed for edge in self.edges if edge.target == node)

    def select(self, state: State) -> int | None:
        if not self.unfinished:
            return self.goal_node
        candidates = [i for i in self.unfinished if self.predecessors_complete(i)]
        return next(
            (i for i in candidates if state.holds(self.plan[i].preconditions)),
            candidates[0] if candidates else None,
        )

    def live_support(self, completed: set[int] | None = None) -> tuple[Edge, ...]:
        completed = self.completed if completed is None else completed
        return tuple(
            edge
            for edge in self.edges
            if edge.condition is not None
            and edge.source in completed
            and edge.target not in completed
        )

    def causal_successors(self, roots: set[int]) -> set[int]:
        reached = set(roots) & set(self.unfinished)
        frontier = list(reached)
        while frontier:
            source = frontier.pop()
            for edge in self.edges:
                if (
                    edge.source == source
                    and edge.condition is not None
                    and edge.target in self.unfinished
                    and edge.target not in reached
                ):
                    reached.add(edge.target)
                    frontier.append(edge.target)
        return reached


def interfere(first: Operator, second: Operator) -> bool:
    return (
        bool(first.resources & second.resources)
        or any(
            literal.negate() in second.preconditions | second.effects for literal in first.effects
        )
        or any(
            literal.negate() in first.preconditions | first.effects for literal in second.effects
        )
    )


def build_graph(state: State, plan: tuple[Operator, ...], goal: frozenset[Literal]) -> TaskGraph:
    if not consistent(goal):
        raise ValueError(goal)
    edges = []
    producers: dict[Literal, int] = {}
    current = state
    for index in range(len(plan) + 1):
        required = goal if index == len(plan) else plan[index].preconditions
        for literal in sorted(required):
            if current.truth(literal) is not True:
                raise ValueError((index, literal))
            edges.append(Edge(producers.get(literal, START), index, literal))
        if index == len(plan):
            break
        action = plan[index]
        for previous in range(index):
            if interfere(plan[previous], action):
                edges.append(Edge(previous, index))
        current = action.apply(current)
        for literal in action.effects:
            producers.pop(literal.negate(), None)
            producers[literal] = index
    return TaskGraph(tuple(plan), goal, tuple(edges))
