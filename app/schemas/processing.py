from datetime import datetime

from pydantic import BaseModel, Field


class ProcessingMetadata(BaseModel):
    original_width: int | None = None
    original_height: int | None = None
    rotation_applied: int = 0
    deskew_angle: float = 0.0
    contrast_normalized: bool = False
    output_format: str = "png"
    output_width: int | None = None
    output_height: int | None = None


class DocumentProcessResponse(BaseModel):
    message: str = "Document processed successfully"
    document_id: str
    status: str
    page_count: int
    pages_processed: int


class DocumentPageItem(BaseModel):
    page_number: int
    width: int | None = None
    height: int | None = None
    mime_type: str = "image/png"
    processing_metadata: dict | None = None


class DocumentPagesResponse(BaseModel):
    document_id: str
    total_pages: int
    pages: list[DocumentPageItem] = Field(default_factory=list)


class DocumentPageResponse(BaseModel):
    document_id: str
    page_number: int
    mime_type: str
    width: int | None = None
    height: int | None = None
    url: str
    expires_in: int
    created_at: datetime | None = None
