# app/models.py


import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# 1. File Status Enum
#    Tracks the lifecycle of a document through the system.
#    PENDING    → Just uploaded, nothing processed yet.
#    PROCESSING → Celery is actively chunking/embedding it.
#    READY      → Vectors are in Qdrant. Safe to query.
#    FAILED     → Something went wrong (bad PDF, API error, etc.)

class FileStatus(str, enum.Enum):
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    READY      = "READY"
    FAILED     = "FAILED"

class File(Base):
    __tablename__ = "files"


    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    org_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # The original filename (e.g., "NDA_v1.pdf")
    filename: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), nullable=False, default=FileStatus.PENDING
    )

    # The full extracted text from the PDF.
    # Stored here so we can re-embed later if we switch models,
    # or show the user the raw text without re-parsing the PDF.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<File id={self.id} filename={self.filename!r} status={self.status}>"
