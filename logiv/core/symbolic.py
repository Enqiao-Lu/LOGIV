from collections.abc import Iterable, Mapping
from dataclasses import dataclass

Atom = tuple[str, ...]


@dataclass(frozen=True, order=True)
class Literal:
    atom: Atom
    positive: bool = True

    def negate(self) -> "Literal":
        return Literal(self.atom, not self.positive)

    def pddl(self) -> str:
        atom = f"({' '.join(self.atom)})"
        return atom if self.positive else f"(not {atom})"


def consistent(literals: Iterable[Literal]) -> bool:
    literals = frozenset(literals)
    return all(literal.negate() not in literals for literal in literals)


@dataclass(frozen=True)
class State:
    facts: frozenset[Atom]
    unknown: frozenset[Atom] = frozenset()

    def truth(self, literal: Literal) -> bool | None:
        if literal.atom in self.unknown:
            return None
        if literal.atom[0] == "=":
            return (literal.atom[1] == literal.atom[2]) == literal.positive
        return (literal.atom in self.facts) == literal.positive

    def holds(self, literals: Iterable[Literal]) -> bool:
        return all(self.truth(literal) is True for literal in literals)

    def update(self, evidence: Mapping[Atom, bool | None]) -> "State":
        facts, unknown = set(self.facts), set(self.unknown)
        for atom, value in evidence.items():
            if value is not None and type(value) is not bool:
                raise ValueError((atom, value))
            facts.discard(atom)
            unknown.discard(atom)
            if value is True:
                facts.add(atom)
            elif value is None:
                unknown.add(atom)
        return State(frozenset(facts), frozenset(unknown))


@dataclass(frozen=True)
class Operator:
    name: str
    arguments: tuple[str, ...]
    preconditions: frozenset[Literal]
    effects: frozenset[Literal]
    resources: frozenset[str] = frozenset()
    instruction: str = ""

    def __post_init__(self):
        if not consistent(self.preconditions) or not consistent(self.effects):
            raise ValueError(self.name)
        if any(literal.atom[0] == "=" for literal in self.effects):
            raise ValueError(self.name)

    @property
    def signature(self) -> tuple[str, ...]:
        return (self.name, *self.arguments)

    def pddl(self) -> str:
        return f"({' '.join(self.signature)})"

    def apply(self, state: State) -> State:
        if not state.holds(self.preconditions):
            raise ValueError(self.pddl())
        return state.update({literal.atom: literal.positive for literal in self.effects})


def backstep(required: frozenset[Literal], action: Operator) -> frozenset[Literal] | None:
    if any(literal.negate() in action.effects for literal in required):
        return None
    result = (required - action.effects) | action.preconditions
    return result if consistent(result) else None


def regress(goal: frozenset[Literal], plan: Iterable[Operator]) -> frozenset[Literal] | None:
    required = goal
    if not consistent(required):
        return None
    for action in reversed(tuple(plan)):
        required = backstep(required, action)
        if required is None:
            return None
    return required
