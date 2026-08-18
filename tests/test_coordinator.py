from backend.models.schemas import CoordinatorClassificationLabel
from backend.qualification.coordinator import classify_title


def test_strong_qualifying_titles():
    for title in [
        "Scheduling Coordinator",
        "Scheduler",
        "Operations Coordinator",
        "Dispatch Coordinator",
        "Dispatcher",
        "Claims Coordinator",
        "Network Coordinator",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.QUALIFIED_COORDINATION, title


def test_schedul_and_dispatch_stem_variants_qualify():
    """Real titles Clay returned for an actual court reporting agency --
    same function, different noun suffix than 'scheduler'/'dispatcher'."""
    for title in [
        "Scheduling Specialist",
        "Senior Scheduling Coordinator",
        "Regional Scheduling Coordinator",
        "Dispatch Specialist",
        "Scheduling Manager",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.QUALIFIED_COORDINATION, title


def test_disqualifying_titles_override_bare_coordinator_word():
    for title in [
        "Marketing Coordinator",
        "HR Coordinator",
        "Recruiting Coordinator",
        "Customer Success Coordinator",
        "Event Coordinator",
        "Project Coordinator",
    ]:
        label, reason, confidence = classify_title(title)
        assert label == CoordinatorClassificationLabel.NOT_QUALIFIED, title


def test_bare_coordinator_is_review_not_auto_qualified():
    label, reason, confidence = classify_title("Coordinator")
    assert label == CoordinatorClassificationLabel.REVIEW


def test_unrelated_title_not_qualified():
    label, reason, confidence = classify_title("Software Engineer")
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED


def test_empty_title_not_qualified():
    label, reason, confidence = classify_title(None)
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED
    label, reason, confidence = classify_title("")
    assert label == CoordinatorClassificationLabel.NOT_QUALIFIED
