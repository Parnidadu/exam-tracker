import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from exams.models import ExamStage


def test_examstage_valid_stage_types_are_accepted(exam):
    for stage_type, _ in ExamStage.StageType.choices:
        stage = ExamStage(exam=exam, stage_type=stage_type, sequence=1)
        stage.full_clean()


def test_examstage_rejects_invalid_stage_type(exam):
    stage = ExamStage(exam=exam, stage_type="written", sequence=1)
    with pytest.raises(ValidationError):
        stage.full_clean()


def test_examstages_are_ordered_by_sequence_within_exam(exam):
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.MAINS, sequence=2)
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.INTERVIEW, sequence=3)
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)

    assert list(ExamStage.objects.filter(exam=exam).values_list("sequence", flat=True)) == [
        1,
        2,
        3,
    ]


def test_examstage_sequence_must_be_unique_within_exam(exam):
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)
    with pytest.raises(IntegrityError):
        ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.MAINS, sequence=1)


def test_examstage_str(exam):
    stage = ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )
    assert str(stage) == f"{exam} - Prelims"
