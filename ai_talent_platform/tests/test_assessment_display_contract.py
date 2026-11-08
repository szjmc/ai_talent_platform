"""Regression coverage for optional assessment deadlines and list metadata."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.assessment import AssessmentCreateRequest
from app.schemas.resume import ResumeResponse


def test_deadline_optional_and_future_timezone_normalized():
    assert AssessmentCreateRequest(file_id=1).deadline is None
    future = datetime.now(timezone.utc) + timedelta(days=2)
    value = AssessmentCreateRequest(file_id=1, deadline=future).deadline
    assert value == future.astimezone().replace(tzinfo=None)
    with pytest.raises(ValidationError):
        AssessmentCreateRequest(file_id=1, deadline=datetime.now(timezone.utc) - timedelta(seconds=1))


def test_resume_list_retains_accuracy_including_zero():
    row = SimpleNamespace(id=1, file_id=1, talent_id=None, file_name='a.pdf', file_type='pdf',
                          file_size=100, status='success', created_at=datetime.now(),
                          parsed_at=None, accuracy=0.0)
    assert ResumeResponse.model_validate(row).model_dump()['accuracy'] == 0.0
    row.accuracy = None
    assert ResumeResponse.model_validate(row).accuracy is None
