"""任务交接数据模型，与传输入口无关。"""
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints, model_validator


class ArtifactRef(BaseModel):
    path: str = Field(min_length=1)     # 相对 workspace_root 的路径
    sha256: Optional[str] = None        # 创建时快照的文件摘要
    note: str = ""


class ValidationEvidence(BaseModel):
    command: str = Field(min_length=1)
    outcome: Literal["passed", "failed", "not_run"]
    detail: str = ""


HandoffText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SnapshotFile(BaseModel):
    path: HandoffText
    sha256: Optional[str] = None  # None means a recorded deletion.
    executable: bool = False


class WorkspaceSnapshot(BaseModel):
    source: Literal["tool_observed"] = "tool_observed"
    captured_at: str
    git_head: Optional[str] = None
    git_branch: Optional[str] = None
    tracked_changes: list[str] = Field(default_factory=list)
    files: list[SnapshotFile] = Field(default_factory=list)
    diff: str = ""
    diff_truncated: bool = False
    excluded: list[str] = Field(default_factory=list)
    untracked_not_included: list[str] = Field(default_factory=list)
    capture_runtime: dict[str, str] = Field(default_factory=dict)


class HandoffCreate(BaseModel):
    schema_version: Literal[1, 2] = 1
    task_id: HandoffText
    from_agent: HandoffText
    to_agent: Optional[HandoffText] = None
    goal: HandoffText
    status_summary: HandoffText
    done_when: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    validation: list[ValidationEvidence] = Field(default_factory=list)
    source_revision: Optional[str] = None
    workspace_root: Optional[str] = None
    # Capture produces v2 so old readers cannot silently ignore this policy.
    require_verification: bool = False
    workspace_snapshot: Optional[WorkspaceSnapshot] = None
    observed_checks: list[dict] = Field(default_factory=list)
    ttl_seconds: int = Field(default=86400, ge=60, le=2592000)

    @model_validator(mode="after")
    def validate_verification_policy(self):
        if self.schema_version == 2 and not self.require_verification:
            raise ValueError("v2 交接包必须要求核验记录")
        return self
