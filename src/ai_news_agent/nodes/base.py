from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class WorkflowContext(BaseModel):
    run_id: str
    workflow_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)

    def with_data(self, **updates: Any) -> "WorkflowContext":
        return self.model_copy(update={"data": {**self.data, **updates}})

    def log(self, message: str) -> "WorkflowContext":
        return self.model_copy(update={"logs": [*self.logs, message]})


class MediaNode(ABC):
    id: str
    name: str
    input_schema: type[BaseModel] = WorkflowContext
    output_schema: type[BaseModel] = WorkflowContext

    @abstractmethod
    def run(self, context: WorkflowContext) -> WorkflowContext:
        raise NotImplementedError
