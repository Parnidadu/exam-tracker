import pytest
from django.db.models import ProtectedError

from exams.models import StatusTrack
from verification.models import VerificationRecord


def _make_record(exam_stage, actor, **overrides):
    fields = {
        "exam_stage": exam_stage,
        "track": StatusTrack.Track.CONDUCT,
        "value": "conducted",
        "evidence_url": "https://upsc.gov.in/notice/123",
        "note": "Confirmed via official notice",
        "actor": actor,
    }
    fields.update(overrides)
    return VerificationRecord.objects.create(**fields)


@pytest.mark.django_db
def test_verification_record_stores_all_fields(exam_stage, actor):
    record = _make_record(exam_stage, actor)
    record.refresh_from_db()

    assert record.exam_stage == exam_stage
    assert record.track == StatusTrack.Track.CONDUCT
    assert record.value == "conducted"
    assert record.evidence_url == "https://upsc.gov.in/notice/123"
    assert record.note == "Confirmed via official notice"
    assert record.actor == actor
    assert record.timestamp is not None


@pytest.mark.django_db
def test_verification_record_cannot_be_updated_via_instance_save(exam_stage, actor):
    record = _make_record(exam_stage, actor)
    record.value = "postponed"
    with pytest.raises(TypeError):
        record.save()


@pytest.mark.django_db
def test_verification_record_cannot_be_deleted_via_instance_delete(exam_stage, actor):
    record = _make_record(exam_stage, actor)
    with pytest.raises(TypeError):
        record.delete()


@pytest.mark.django_db
def test_verification_record_queryset_update_is_blocked(exam_stage, actor):
    _make_record(exam_stage, actor)
    with pytest.raises(TypeError):
        VerificationRecord.objects.filter(exam_stage=exam_stage).update(value="postponed")


@pytest.mark.django_db
def test_verification_record_queryset_delete_is_blocked(exam_stage, actor):
    _make_record(exam_stage, actor)
    with pytest.raises(TypeError):
        VerificationRecord.objects.filter(exam_stage=exam_stage).delete()


@pytest.mark.django_db
def test_verification_record_protects_exam_stage_from_deletion(exam_stage, actor):
    _make_record(exam_stage, actor)
    with pytest.raises(ProtectedError):
        exam_stage.delete()


@pytest.mark.django_db
def test_verification_record_protects_actor_from_deletion(exam_stage, actor):
    _make_record(exam_stage, actor)
    with pytest.raises(ProtectedError):
        actor.delete()
