import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from ..core.symbolic import Atom
from ..core.verification import Observation


@dataclass
class GPT4o:
    client: Any
    predicate_descriptions: dict[str, str]
    object_descriptions: dict[str, str]
    model: str = "gpt-4o"
    system_prompt: str = field(
        default_factory=lambda: (
            files("logiv")
            .joinpath("prompts/state_assessment.txt")
            .read_text(encoding="utf-8")
            .strip()
        )
    )

    def assess(self, observation: Observation, atoms: tuple[Atom, ...]) -> dict[Atom, bool | None]:
        from openai import APIConnectionError, InternalServerError, RateLimitError

        if not atoms:
            return {}
        unknown = dict.fromkeys(atoms)
        payload = {
            "predicates": self.predicate_descriptions,
            "objects": self.object_descriptions,
            "queries": ["(" + " ".join(atom) + ")" for atom in atoms],
            "robot": dict(observation.robot),
        }
        content = [{"type": "text", "text": json.dumps(payload)}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image}} for image in observation.images
        )
        try:
            response = self.client.with_options(max_retries=0).chat.completions.create(
                model=self.model,
                max_tokens=512,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt,
                    },
                    {"role": "user", "content": content},
                ],
            )
        except (APIConnectionError, InternalServerError, RateLimitError):
            return unknown
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            return unknown
        try:
            payload = json.loads(choice.message.content or "")
        except (TypeError, ValueError):
            return unknown
        values = payload.get("facts") if isinstance(payload, dict) else None
        if not isinstance(values, list) or len(values) != len(atoms):
            return unknown
        if any(value is not None and type(value) is not bool for value in values):
            return unknown
        return dict(zip(atoms, values))
