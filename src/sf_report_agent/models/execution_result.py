from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    task_id: int
    status: str
    row_count: int = 0
    artifacts: list[str] = Field(default_factory=list)
    response_text: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

