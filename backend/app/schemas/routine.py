"""Schemas de importação de rotina por inferência (texto/imagem → grade+atividades+disponibilidade)."""
from typing import Optional
from pydantic import BaseModel, Field


class RoutineSchedule(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: str
    end_time: str
    subject: str = "Aula"
    kind: str = "aula"


class RoutineActivity(BaseModel):
    title: str
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    start_time: str
    end_time: str
    recurrence: str = "weekly"
    travel_before_min: int = 0
    travel_after_min: int = 0
    is_blocking: bool = True


class ParentWindow(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: str
    end_time: str


class RoutineExtractionResult(BaseModel):
    schedules: list[RoutineSchedule] = Field(default_factory=list)
    activities: list[RoutineActivity] = Field(default_factory=list)
    availability: list[ParentWindow] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = True
    warnings: list[str] = Field(default_factory=list)
