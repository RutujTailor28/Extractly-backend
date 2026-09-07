from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocumentStatus = Literal[
    "queued",
    "preprocessing",
    "extracting",
    "validating",
    "ready",
    "needs_review",
    "approved",
    "failed",
]

DocumentSource = Literal["upload", "api", "email"]
SortByField = Literal["created_at", "filename", "status"]
SortOrder = Literal["asc", "desc"]

ALLOWED_STATUSES: set[str] = {
    "queued",
    "preprocessing",
    "extracting",
    "validating",
    "ready",
    "needs_review",
    "approved",
    "failed",
}


class DocumentUploadItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    file_size: int
    status: str
    source: str
    created_at: datetime | None = None


class UploadErrorItem(BaseModel):
    filename: str
    error: str


class DocumentUploadResponse(BaseModel):
    message: str = "Upload completed"
    successful: int
    failed: int
    documents: list[DocumentUploadItem] = Field(default_factory=list)
    errors: list[UploadErrorItem] = Field(default_factory=list)


class DocumentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    file_size: int | None = None
    page_count: int | None = 1
    status: str
    source: str
    created_at: datetime | None = None
    vendor_name: str | None = None
    invoice_number: str | None = None
    total: float | None = None
    overall_confidence: float | None = None
    has_flags: bool = False


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    pagination: PaginationMeta


class DocumentCore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    file_size: int | None = None
    page_count: int | None = 1
    status: str
    source: str
    storage_path: str | None = None
    file_hash: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractionSummary(BaseModel):
    id: str
    schema_version: str | None = None
    model: str | None = None
    overall_confidence: float | None = None
    created_at: datetime | None = None


class FieldValueItem(BaseModel):
    id: str
    field_name: str
    value: str | None = None
    confidence: float | None = None
    page_number: int | None = None
    corrected_value: str | None = None


class ValidationFlagItem(BaseModel):
    id: str
    rule_code: str
    severity: str
    message: str
    resolved: bool = False
    created_at: datetime | None = None


class DocumentDetailResponse(BaseModel):
    document: DocumentCore
    extraction: ExtractionSummary | None = None
    fields: list[FieldValueItem] = Field(default_factory=list)
    validation_flags: list[ValidationFlagItem] = Field(default_factory=list)


class DocumentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DocumentStatus | None = None
    source: DocumentSource | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "DocumentUpdateRequest":
        if self.status is None and self.source is None:
            raise ValueError("At least one of status or source must be provided")
        return self


class DocumentUpdateResponse(BaseModel):
    id: str
    filename: str
    status: str
    updated_at: datetime | None = None


class DocumentDeleteResponse(BaseModel):
    message: str = "Document deleted successfully"
    id: str


class DocumentFileResponse(BaseModel):
    document_id: str
    filename: str
    mime_type: str
    url: str
    expires_in: int


class DocumentListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    status: DocumentStatus | None = None
    search: str | None = None
    vendor: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    flagged: bool | None = None
    sort_by: SortByField = "created_at"
    sort_order: SortOrder = "desc"

    @model_validator(mode="after")
    def validate_date_range(self) -> "DocumentListQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be less than or equal to date_to")
        return self

    @field_validator("search")
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
