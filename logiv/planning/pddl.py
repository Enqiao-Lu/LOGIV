import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from itertools import product
from pathlib import Path

from ..core.symbolic import Literal, Operator, State, consistent


def parse(text: str) -> list:
    tokens = re.findall(r"\(|\)|[^\s()]+", re.sub(r";[^\n]*", "", text).lower())
    stack: list[list] = [[]]
    for token in tokens:
        if token == "(":
            child = []
            stack[-1].append(child)
            stack.append(child)
        elif token == ")":
            if len(stack) == 1:
                raise ValueError(token)
            stack.pop()
        else:
            stack[-1].append(token)
    if len(stack) != 1 or len(stack[0]) != 1 or not isinstance(stack[0][0], list):
        raise ValueError("PDDL")
    return stack[0][0]


def typed_names(tokens: list) -> dict[str, str]:
    result, pending = {}, []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not isinstance(token, str):
            raise ValueError(token)
        if token == "-":
            if not pending or index + 1 == len(tokens) or not isinstance(tokens[index + 1], str):
                raise ValueError(tokens)
            result.update((name, tokens[index + 1]) for name in pending)
            pending.clear()
            index += 2
        else:
            if token in result or token in pending:
                raise ValueError(token)
            pending.append(token)
            index += 1
    result.update((name, "object") for name in pending)
    return result


def literals(expression: list) -> frozenset[Literal]:
    if not expression:
        return frozenset()
    clauses = expression[1:] if expression[0] == "and" else [expression]
    result = set()
    for clause in clauses:
        if not isinstance(clause, list) or not clause:
            raise ValueError(clause)
        positive = clause[0] != "not"
        if not positive and (len(clause) != 2 or not isinstance(clause[1], list)):
            raise ValueError(clause)
        atom = clause if positive else clause[1]
        if not atom or any(not isinstance(token, str) for token in atom):
            raise ValueError(atom)
        if atom[0] in {
            "and",
            "or",
            "not",
            "when",
            "forall",
            "exists",
            "imply",
            "increase",
            "assign",
        }:
            raise ValueError(atom)
        result.add(Literal(tuple(atom), positive))
    if not consistent(result):
        raise ValueError(expression)
    return frozenset(result)


@dataclass(frozen=True)
class ActionSchema:
    name: str
    parameters: dict[str, str]
    preconditions: frozenset[Literal]
    effects: frozenset[Literal]
    instruction: str
    resources: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    domain_text: str
    domain_name: str
    name: str
    types: dict[str, str]
    constants: dict[str, str]
    objects: dict[str, str]
    predicates: dict[str, tuple[str, ...]]
    actions: dict[str, ActionSchema]
    initial_state: State
    goal: frozenset[Literal]

    @cached_property
    def static_predicates(self) -> frozenset[str]:
        changed = {
            literal.atom[0] for action in self.actions.values() for literal in action.effects
        }
        return frozenset(self.predicates.keys() - changed)

    @cached_property
    def dynamic_atoms(self) -> frozenset[tuple[str, ...]]:
        objects = self.constants | self.objects
        return frozenset(
            (predicate, *arguments)
            for predicate, kinds in self.predicates.items()
            if predicate not in self.static_predicates
            for arguments in product(
                *[
                    [name for name, actual in objects.items() if self.is_type(actual, kind)]
                    for kind in kinds
                ]
            )
        )

    def is_type(self, actual: str, expected: str) -> bool:
        visited = set()
        while actual != expected:
            if actual == "object" or actual in visited or actual not in self.types:
                return False
            visited.add(actual)
            actual = self.types[actual]
        return True

    def check_atom(self, atom: tuple[str, ...], variables: Mapping[str, str] | None = None):
        bindings = self.constants | self.objects | dict(variables or {})
        if atom[0] == "=":
            if len(atom) != 3 or any(value not in bindings for value in atom[1:]):
                raise ValueError(atom)
            return
        expected = self.predicates.get(atom[0])
        if expected is None or len(expected) != len(atom) - 1:
            raise ValueError(atom)
        for name, kind in zip(atom[1:], expected):
            if name not in bindings or not self.is_type(bindings[name], kind):
                raise ValueError(atom)

    def ground(self, signature: tuple[str, ...]) -> Operator:
        schema = self.actions[signature[0]]
        arguments = signature[1:]
        if len(arguments) != len(schema.parameters):
            raise ValueError(signature)
        objects = self.constants | self.objects
        for value, kind in zip(arguments, schema.parameters.values()):
            if value not in objects or not self.is_type(objects[value], kind):
                raise ValueError(signature)
        bindings = dict(zip(schema.parameters, arguments))

        def substitute(conditions):
            return frozenset(
                Literal(tuple(bindings.get(part, part) for part in literal.atom), literal.positive)
                for literal in conditions
            )

        return Operator(
            schema.name,
            arguments,
            substitute(schema.preconditions),
            substitute(schema.effects),
            frozenset(bindings.get(resource, resource) for resource in schema.resources),
            schema.instruction.format_map(
                {key.lstrip("?"): value for key, value in bindings.items()}
            ),
        )

    def problem(self, state: State, goal: frozenset[Literal]) -> str:
        if state.unknown or not consistent(goal):
            raise ValueError((state.unknown, goal))
        for atom in state.facts | {literal.atom for literal in goal}:
            self.check_atom(atom)
        objects = " ".join(f"{name} - {kind}" for name, kind in sorted(self.objects.items()))
        initial = " ".join(Literal(atom).pddl() for atom in sorted(state.facts))
        target = " ".join(literal.pddl() for literal in sorted(goal))
        return (
            f"(define (problem {self.name}) (:domain {self.domain_name})\n"
            f"(:objects {objects})\n(:init {initial})\n(:goal (and {target})))\n"
        )


def load_task(
    domain: str | Path,
    problem: str | Path,
    instructions: Mapping[str, str],
    resources: Mapping[str, tuple[str, ...]],
) -> Task:
    domain_text = Path(domain).read_text()
    domain_tree = parse(domain_text)
    problem_tree = parse(Path(problem).read_text())
    if domain_tree[:1] != ["define"] or problem_tree[:1] != ["define"]:
        raise ValueError("define")
    if domain_tree[1][0] != "domain" or problem_tree[1][0] != "problem":
        raise ValueError("domain/problem")
    sections = {}
    actions = {}
    for section in domain_tree[2:]:
        if section[0] == ":action":
            name = section[1]
            if name in actions or name not in instructions or name not in resources:
                raise ValueError(name)
            fields = dict(zip(section[2::2], section[3::2]))
            if set(fields) != {":parameters", ":precondition", ":effect"}:
                raise ValueError(name)
            actions[name] = ActionSchema(
                name,
                typed_names(fields[":parameters"]),
                literals(fields[":precondition"]),
                literals(fields[":effect"]),
                instructions[name],
                tuple(resources[name]),
            )
        else:
            if section[0] in sections:
                raise ValueError(section[0])
            sections[section[0]] = section[1:]
    if set(sections) - {":requirements", ":types", ":constants", ":predicates"}:
        raise ValueError(set(sections))
    if set(sections.get(":requirements", [])) - {
        ":strips",
        ":typing",
        ":negative-preconditions",
        ":equality",
    }:
        raise ValueError(sections[":requirements"])
    problem_sections = {section[0]: section[1:] for section in problem_tree[2:]}
    if set(problem_sections) - {":domain", ":objects", ":init", ":goal"}:
        raise ValueError(set(problem_sections))
    if problem_sections[":domain"] != [domain_tree[1][1]]:
        raise ValueError(problem_sections[":domain"])
    initial = literals(["and", *problem_sections[":init"]])
    task = Task(
        domain_text,
        domain_tree[1][1],
        problem_tree[1][1],
        typed_names(sections.get(":types", [])),
        typed_names(sections.get(":constants", [])),
        typed_names(problem_sections.get(":objects", [])),
        {entry[0]: tuple(typed_names(entry[1:]).values()) for entry in sections[":predicates"]},
        actions,
        State(frozenset(literal.atom for literal in initial if literal.positive)),
        literals(problem_sections[":goal"][0]),
    )
    for kind in {
        *task.types,
        *task.types.values(),
        *task.constants.values(),
        *task.objects.values(),
    }:
        if not task.is_type(kind, "object"):
            raise ValueError(kind)
    if task.constants.keys() & task.objects.keys():
        raise ValueError(task.constants.keys() & task.objects.keys())
    for literal in initial | task.goal:
        task.check_atom(literal.atom)
    for schema in actions.values():
        if not schema.instruction.strip():
            raise ValueError(schema.name)
        for literal in schema.preconditions | schema.effects:
            task.check_atom(literal.atom, schema.parameters)
        if any(literal.atom[0] == "=" for literal in schema.effects):
            raise ValueError(schema.name)
        for resource in schema.resources:
            if resource.startswith("?") and resource not in schema.parameters:
                raise ValueError(resource)
    return task
