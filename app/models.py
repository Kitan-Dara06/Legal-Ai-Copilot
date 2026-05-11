import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserRole(str, enum.Enum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    PARTNER = "PARTNER"
    ASSOCIATE = "ASSOCIATE"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"

    @classmethod
    def hierarchy(cls) -> dict:
        """
        Returns a mapping of each role to its permission level (higher = more access).
        Used by the RBAC middleware to enforce endpoint permissions.
        """
        return {
            cls.SYSTEM_ADMIN: 100,
            cls.PARTNER: 80,
            cls.ADMIN: 70,
            cls.ASSOCIATE: 50,
            cls.MEMBER: 30,
        }

    def meets_threshold(self, required: "UserRole") -> bool:
        """True if this role's level >= required role's level."""
        return self.hierarchy().get(self, 0) >= self.hierarchy().get(required, 0)


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


class DocumentType(str, enum.Enum):
    DIGITAL_PDF = "DIGITAL_PDF"
    SCANNED_PDF = "SCANNED_PDF"
    DOCX = "DOCX"


class IntelligenceStatus(str, enum.Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    READY = "READY"


class IntentType(str, enum.Enum):
    ANALYZE = "ANALYZE"
    REASON = "REASON"
    ACT = "ACT"
    UNKNOWN = "UNKNOWN"


class WorkflowStatus(str, enum.Enum):
    CLASSIFYING = "CLASSIFYING"
    AWAITING_INTENT_CONFIRMATION = "AWAITING_INTENT_CONFIRMATION"
    RETRIEVING = "RETRIEVING"
    EXPANDING = "EXPANDING"
    REASONING = "REASONING"
    DRAFTING = "DRAFTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    REVISING = "REVISING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class GoalStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EscalationType(str, enum.Enum):
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    DEFINITIONAL_CONFLICT = "DEFINITIONAL_CONFLICT"
    STRUCTURAL_AMBIGUITY = "STRUCTURAL_AMBIGUITY"


class ActionType(str, enum.Enum):
    DRAFT_RESPONSE = "DRAFT_RESPONSE"
    FILE_DOCUMENT = "FILE_DOCUMENT"
    SEND_NOTICE = "SEND_NOTICE"
    UPDATE_CASE_TRACKER = "UPDATE_CASE_TRACKER"
    SET_REMINDER = "SET_REMINDER"
    ESCALATE_TO_COUNSEL = "ESCALATE_TO_COUNSEL"


class ActionStatus(str, enum.Enum):
    DETECTED = "DETECTED"
    CONFIRMED = "CONFIRMED"
    DRAFTING = "DRAFTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    USED = "USED"


class ObligationType(str, enum.Enum):
    RESPONSE = "RESPONSE"
    FILING = "FILING"
    NOTICE = "NOTICE"
    PAYMENT = "PAYMENT"
    OTHER = "OTHER"


class DeadlineResolutionStatus(str, enum.Enum):
    RESOLVED = "RESOLVED"
    RELATIVE_UNRESOLVED = "RELATIVE_UNRESOLVED"
    CONFLICTED = "CONFLICTED"


class DeadlineStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"


class IdempotencyClass(str, enum.Enum):
    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    REQUIRES_COMPENSATION = "REQUIRES_COMPENSATION"


class ToolCallStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    UNCOMPENSATABLE_FAILURE = "UNCOMPENSATABLE_FAILURE"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    supabase_user_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.MEMBER
    )
    personal_org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=True, index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class UserOrgMembership(Base):
    __tablename__ = "user_org_memberships"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "org_id", name="pk_user_org_memberships"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.MEMBER
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    prefix: Mapped[str] = mapped_column(String(50), nullable=False)
    key_hash: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Invite(Base):
    __tablename__ = "organization_invites"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.MEMBER
    )
    token: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intelligence_status: Mapped[IntelligenceStatus] = mapped_column(
        Enum(IntelligenceStatus), nullable=False, default=IntelligenceStatus.PENDING
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkspaceSession(Base):
    __tablename__ = "workspace_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    document_subset: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(Uuid), nullable=True
    )
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_type: Mapped[DocumentType | None] = mapped_column(
        Enum(DocumentType), nullable=True
    )
    r2_key: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), nullable=False, default=DocumentStatus.PENDING
    )
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intelligence_stages_complete: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # Legacy


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspace_sessions.id"), nullable=True, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    goal_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[GoalStatus] = mapped_column(
        Enum(GoalStatus), nullable=False, default=GoalStatus.PENDING
    )
    intent: Mapped[IntentType | None] = mapped_column(Enum(IntentType), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_subset: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(Uuid), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("goals.id"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    intent: Mapped[IntentType] = mapped_column(
        Enum(IntentType), nullable=False, default=IntentType.UNKNOWN
    )
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_confirmed_by_human: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    confirmed_intent: Mapped[IntentType | None] = mapped_column(
        Enum(IntentType, name="confirmed_intent_type"), nullable=True
    )
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus), nullable=False, default=WorkflowStatus.CLASSIFYING
    )
    langgraph_checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    supporting_citations: Mapped[list[dict] | None] = mapped_column(
        JSONB, nullable=True
    )
    reference_chain: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    definitional_conflicts: Mapped[list[dict] | None] = mapped_column(
        JSONB, nullable=True
    )
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_type: Mapped[EscalationType | None] = mapped_column(
        Enum(EscalationType), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    urgency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ActionStatus] = mapped_column(
        Enum(ActionStatus), nullable=False, default=ActionStatus.DETECTED
    )
    source_clause_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    draft_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    idempotency_class: Mapped[IdempotencyClass] = mapped_column(
        Enum(IdempotencyClass), nullable=False, default=IdempotencyClass.IDEMPOTENT
    )
    compensation_action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    compensation_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deadline_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("deadline_registry.id"), nullable=True
    )
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("actions.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING
    )
    actor: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    decision_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft_r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("actions.id"), nullable=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    llm_prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_input_r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    full_output_r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_reference_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DefinedTermRegistry(Base):
    __tablename__ = "defined_terms_registry"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    term: Mapped[str] = mapped_column(String(500), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    clause_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conflict_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class DeadlineRegistry(Base):
    __tablename__ = "deadline_registry"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    obligation_description: Mapped[str] = mapped_column(Text, nullable=False)
    obligation_type: Mapped[ObligationType] = mapped_column(
        Enum(ObligationType), nullable=False
    )
    raw_date_expression: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_status: Mapped[DeadlineResolutionStatus] = mapped_column(
        Enum(DeadlineResolutionStatus),
        nullable=False,
        default=DeadlineResolutionStatus.RELATIVE_UNRESOLVED,
    )
    conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_clause_a: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_clause_b: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[DeadlineStatus] = mapped_column(
        Enum(DeadlineStatus), nullable=False, default=DeadlineStatus.ACTIVE
    )
    urgency_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ToolCallLog(Base):
    __tablename__ = "tool_call_log"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("actions.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ToolCallStatus] = mapped_column(
        Enum(ToolCallStatus), nullable=False, default=ToolCallStatus.PENDING
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class IntentLog(Base):
    """NFR-AUD-03: Intent classification audit trail."""

    __tablename__ = "intent_log"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=False, index=True
    )
    goal_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_id: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confirmed_intent: Mapped[str] = mapped_column(String(20), nullable=False)
    lawyer_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Notification(Base):
    """Ephemeral, user-scoped in-app notification.

    This is NOT the AuditLog. Notifications are:
      - Ephemeral: user can dismiss them
      - User-scoped: tied to a specific user
      - Actionable: includes action_url to navigate to relevant page
      - Fallback: only created when Slack/Email delivery fails

    FR-NOTIF-01: In-app fallback for failed notification delivery.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="info"
    )
    action_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Frontend URL to navigate to (e.g., /workspaces/{id}/goals/{goal_id})",
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
