from pydantic import BaseModel, Field


class VariantExecutionResult(BaseModel):
    variant_id: str
    variant_label: str
    interpretation: str | None = None
    soql: str
    row_count: int = 0
    artifacts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    task_id: int
    status: str
    soql: str = ""
    row_count: int = 0
    artifacts: list[str] = Field(default_factory=list)
    response_text: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    variants: list[VariantExecutionResult] = Field(default_factory=list)
