import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from ..core.symbolic import Atom, Operator
from ..core.verification import Observation
from .bicoord import TASKS as BICOORD_TASKS
from .robotwin import TASKS as ROBOTWIN_TASKS

TASKS = {"robotwin": ROBOTWIN_TASKS, "bicoord": BICOORD_TASKS}


@dataclass(frozen=True)
class ActionChunk:
    actions: Sequence[Any]
    done: bool = False


class Policy(Protocol):
    def reset(self): ...

    def predict(self, observation: Mapping[str, Any], instruction: str) -> ActionChunk: ...


def json_value(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def image_url(rgb) -> str:
    from PIL import Image

    image = rgb if isinstance(rgb, Image.Image) else Image.fromarray(rgb)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


@dataclass
class BenchmarkRuntime:
    benchmark: str
    task: str
    environment: Any
    policy: Policy
    action_budget: int
    feedback: Callable[[Mapping[str, Any]], Mapping[Atom, bool | None]] | None = None
    active_perception: Callable[[Any], None] | None = None
    action_type: str = "qpos"

    def __post_init__(self):
        if self.benchmark not in TASKS or self.task not in TASKS[self.benchmark]:
            raise ValueError((self.benchmark, self.task))
        if self.action_budget < 1 or self.action_type not in {"qpos", "ee"}:
            raise ValueError((self.action_budget, self.action_type))

    def observe(self, active: bool = False) -> Observation:
        if active and self.active_perception is not None:
            self.active_perception(self.environment)
        raw = self.environment.get_obs()
        images = tuple(
            image_url(camera["rgb"]) for camera in raw["observation"].values() if "rgb" in camera
        )
        if not images:
            raise ValueError("RGB")
        robot = {name: json_value(raw[name]) for name in ("joint_action", "endpose") if name in raw}
        feedback = self.feedback(raw) if self.feedback is not None else {}
        return Observation(images, robot, feedback, raw)

    def execute(self, action: Operator, observation: Observation) -> Observation:
        if not action.instruction.strip():
            raise ValueError(action.signature)
        self.environment.set_instruction(action.instruction)
        self.policy.reset()
        steps = 0
        raw = observation.raw
        while steps < self.action_budget:
            chunk = self.policy.predict(raw, action.instruction)
            if not len(chunk.actions) and not chunk.done:
                raise ValueError("EMPTY_ACTION_CHUNK")
            for control in chunk.actions:
                self.environment.take_action(control, action_type=self.action_type)
                steps += 1
                if steps == self.action_budget:
                    break
            if chunk.done or steps == self.action_budget:
                break
            raw = self.environment.get_obs()
        return self.observe()
