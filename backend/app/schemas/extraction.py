"""Schemas de extração (SPECS §5.4 v1.1). Contrato interno validado por Pydantic."""
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


SUBJECTS = [
    "Matemática", "Português", "Redação", "Ciências", "Biologia",
    "Física", "Química", "História", "Geografia", "Inglês", "Espanhol",
    "Artes", "Educação Física", "Ensino Religioso", "Outro",
]


class ExtractionResult(BaseModel):
    is_homework: bool = True
    subject: Optional[str] = None
    title: Optional[str] = None
    statement: Optional[str] = None
    due_at: Optional[str] = None
    estimated_minutes: Optional[int] = None
    priority: int = Field(default=1, ge=0, le=2)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = True
    extraction_status: str = "ok"
    meta: Dict[str, Any] = Field(default_factory=dict)
