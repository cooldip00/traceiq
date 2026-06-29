from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class DocType(str, Enum):
    LVC = "LVC"
    TIA = "TIA"
    BRD = "BRD"
    CDD = "CDD"
    PRD = "PRD"
    SDD = "SDD"
    ADD = "ADD"
    EPIC = "EPIC"
    USER_STORY = "USER_STORY"


class AcceptanceCriterion(BaseModel):
    ac_id: str
    description: str


class Requirement(BaseModel):
    req_id: str
    title: str
    priority: Optional[str] = None
    status: Optional[str] = None
    description: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    traces_to: list[str] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    doc_id: str
    doc_type: DocType
    title: str
    version: str
    author: Optional[str] = None
    date: Optional[str] = None
    links_to: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    raw_content: str = ""
    filename: str = ""


class IssueSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IssueType(str, Enum):
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    MISSING_LINK = "MISSING_LINK"
    ORPHANED_REQUIREMENT = "ORPHANED_REQUIREMENT"
    MISSING_ACCEPTANCE_CRITERIA = "MISSING_ACCEPTANCE_CRITERIA"
    BROKEN_REFERENCE = "BROKEN_REFERENCE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    COVERAGE_GAP = "COVERAGE_GAP"


class Issue(BaseModel):
    issue_id: str
    issue_type: IssueType
    severity: IssueSeverity
    source_doc: str
    source_req: Optional[str] = None
    target_doc: Optional[str] = None
    target_req: Optional[str] = None
    title: str
    description: str
    ai_reason: Optional[str] = None
    confidence: Optional[float] = None


class StepEvent(BaseModel):
    step: int
    name: str
    status: str  # "running" | "complete" | "error"
    message: str
    data: dict = Field(default_factory=dict)


class AnalysisReport(BaseModel):
    job_id: str
    total_documents: int
    total_requirements: int
    total_links: int
    conflicts: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    ok_count: int = 0
    summary: str = ""
