from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from research_inter_bridge import db
from research_inter_bridge.errors import ProfileError, ProfileNotFoundError
from research_inter_bridge.profiles import (
    DegreeType,
    StudentProfile,
    add_profile_fact,
    completeness,
    confirmed_facts,
    create_user,
    delete_user,
    list_profile_facts,
    load_profile,
    save_profile,
    set_fact_status,
)


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def _profile(user_id: int, **overrides) -> StudentProfile:
    values = {
        "user_id": user_id,
        "full_name": "Asha Verma",
        "contact_email": "asha@example.edu",
    }
    values.update(overrides)
    return StudentProfile(**values)


# --------------------------------------------------------------------------- model


def test_profile_normalises_text_and_lists() -> None:
    profile = _profile(
        1,
        full_name="  Asha   Verma  ",
        contact_email="  ASHA@Example.EDU ",
        coursework=["Machine Learning", " machine learning ", "", "Statistics"],
        skills=("Python", "python", "PyTorch"),
    )

    assert profile.full_name == "Asha Verma"
    assert profile.contact_email == "asha@example.edu"
    assert profile.coursework == ("Machine Learning", "Statistics")
    assert profile.skills == ("Python", "PyTorch")
    assert profile.display_name == "Asha Verma"


def test_preferred_name_wins_for_display() -> None:
    assert _profile(1, preferred_name="Ashu").display_name == "Ashu"


@pytest.mark.parametrize("bad_email", ["", "asha@", "asha.example.edu", "a b@example.edu"])
def test_invalid_email_is_rejected(bad_email: str) -> None:
    with pytest.raises(ProfileError, match="contact_email"):
        _profile(1, contact_email=bad_email)


def test_blank_full_name_is_rejected() -> None:
    with pytest.raises(ProfileError, match="full_name"):
        _profile(1, full_name="   ")


def test_unknown_degree_type_lists_allowed_values() -> None:
    with pytest.raises(ProfileError) as excinfo:
        _profile(1, degree_type="bachelor-ish")

    message = str(excinfo.value)
    assert "degree_type" in message
    assert "btech" in message and "phd" in message


def test_degree_type_accepts_string_enum_and_is_case_insensitive() -> None:
    assert _profile(1, degree_type="BTech").degree_type is DegreeType.BTECH
    assert _profile(1, degree_type=DegreeType.PHD).degree_type is DegreeType.PHD


@pytest.mark.parametrize("year", [0, 9, -1])
def test_current_year_out_of_range_is_rejected(year: int) -> None:
    with pytest.raises(ProfileError, match="current_year"):
        _profile(1, current_year=year)


def test_gpa_must_fit_scale() -> None:
    with pytest.raises(ProfileError, match="exceed"):
        _profile(1, gpa=11.0, gpa_scale=10.0)

    with pytest.raises(ProfileError, match="greater than 0"):
        _profile(1, gpa=8.0, gpa_scale=0)

    with pytest.raises(ProfileError, match="negative"):
        _profile(1, gpa=-1.0)


def test_internship_window_must_be_ordered() -> None:
    with pytest.raises(ProfileError, match="on or before"):
        _profile(1, internship_start="2027-06-01", internship_end="2027-05-01")


def test_invalid_date_is_rejected_with_format_hint() -> None:
    with pytest.raises(ProfileError, match="YYYY-MM-DD"):
        _profile(1, internship_start="June 2027")


def test_too_many_list_items_are_rejected() -> None:
    with pytest.raises(ProfileError, match="at most"):
        _profile(1, skills=[f"skill-{i}" for i in range(51)])


def test_non_string_list_entry_is_rejected() -> None:
    with pytest.raises(ProfileError, match="strings"):
        _profile(1, skills=["python", 42])  # type: ignore[list-item]


def test_with_changes_revalidates() -> None:
    profile = _profile(1)

    updated = profile.with_changes(current_year=3)

    assert updated.current_year == 3
    assert profile.current_year is None
    with pytest.raises(ProfileError):
        profile.with_changes(contact_email="nope")


# ------------------------------------------------------------------ completeness


def test_completeness_reports_missing_fields_for_minimal_profile() -> None:
    report = completeness(_profile(1))

    assert report.percent < 30
    assert "research interests" in report.missing
    assert "document-processing consent" in report.missing
    assert report.is_complete is False


def test_complete_profile_is_100_percent() -> None:
    profile = _profile(
        1,
        institution="IIT Delhi",
        department="Computer Science",
        country="India",
        degree_type="btech",
        current_year=3,
        expected_graduation="2027-05-31",
        gpa=8.9,
        gpa_scale=10.0,
        coursework=["Machine Learning"],
        skills=["Python"],
        research_keywords=["federated learning"],
        target_countries=["Germany"],
        internship_start="2027-05-01",
        internship_end="2027-07-31",
        consent_given_at="2026-10-30T00:00:00+00:00",
    )

    report = completeness(profile)

    assert report.percent == 100
    assert report.missing == ()
    assert report.is_complete is True


# -------------------------------------------------------------------- persistence


def test_create_and_load_profile_roundtrip(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    stored = save_profile(
        connection,
        _profile(
            user_id,
            institution="IIT Delhi",
            department="Computer Science",
            country="India",
            degree_type="btech",
            current_year=3,
            expected_graduation=date(2027, 5, 31),
            gpa=8.9,
            gpa_scale=10.0,
            coursework=("Machine Learning",),
            skills=("Python", "PyTorch"),
            research_keywords=("federated learning",),
            target_countries=("Germany",),
            internship_start=date(2027, 5, 1),
            internship_end=date(2027, 7, 31),
            funding_required=True,
            consent_given_at="2026-10-30T00:00:00+00:00",
        ),
        actor="student:1",
    )

    assert stored.created_at and stored.updated_at
    loaded = load_profile(connection, user_id)
    assert loaded.full_name == "Asha Verma"
    assert loaded.degree_type is DegreeType.BTECH
    assert loaded.expected_graduation == date(2027, 5, 31)
    assert loaded.skills == ("Python", "PyTorch")
    assert loaded.funding_required is True
    assert loaded.created_at == stored.created_at


def test_saving_twice_updates_without_losing_created_at(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    first = save_profile(connection, _profile(user_id), actor="student:1")
    second = save_profile(connection, _profile(user_id, gpa=9.0, gpa_scale=10.0), actor="student:1")

    assert second.created_at == first.created_at
    assert second.updated_at is not None
    assert load_profile(connection, user_id).gpa == 9.0


def test_profile_cannot_be_saved_for_unknown_user(connection: sqlite3.Connection) -> None:
    with pytest.raises(ProfileError, match="create the user first"):
        save_profile(connection, _profile(999), actor="student:999")


def test_profiles_are_isolated_per_user(connection: sqlite3.Connection) -> None:
    first = create_user(connection)
    second = create_user(connection)
    save_profile(connection, _profile(first, full_name="Asha"), actor="student:1")
    save_profile(connection, _profile(second, full_name="Ravi"), actor="student:2")

    assert load_profile(connection, first).full_name == "Asha"
    assert load_profile(connection, second).full_name == "Ravi"

    delete_user(connection, first, actor="student:1")
    assert load_profile(connection, second).full_name == "Ravi"
    with pytest.raises(ProfileNotFoundError):
        load_profile(connection, first)


def test_delete_user_is_idempotent(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")

    assert delete_user(connection, user_id, actor="student:1") is True
    assert delete_user(connection, user_id, actor="student:1") is False


def test_delete_user_cascades_facts_and_audit_records_it(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")
    add_profile_fact(
        connection,
        user_id=user_id,
        field_name="gpa",
        value="8.9",
        source="document:1",
    )

    delete_user(connection, user_id, actor="student:1")

    remaining = connection.execute("SELECT COUNT(*) AS c FROM profile_facts").fetchone()["c"]
    assert remaining == 0
    actions = [event.action for event in db.list_audit_events(connection)]
    assert actions[0] == "user.deleted"
    assert "profile.created" in actions


def test_audit_metadata_never_contains_profile_values(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id, phone="+91-9999999999"), actor="student:1")

    event = db.list_audit_events(connection)[0]
    serialised = repr(event.metadata)
    assert event.action == "profile.created"
    assert "asha@example.edu" not in serialised
    assert "9999999999" not in serialised
    assert event.target_type == "student_profile"


# -------------------------------------------------------------------------- facts


def test_pending_fact_is_not_a_confirmed_fact(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")
    add_profile_fact(
        connection,
        user_id=user_id,
        field_name="research_keyword",
        value="federated learning",
        source="document:1",
        confidence=0.6,
    )

    assert len(confirmed_facts(connection, user_id)) == 0
    assert len(list_profile_facts(connection, user_id, status="pending")) == 1


def test_confirming_a_fact_makes_it_usable_and_is_scoped_to_the_owner(
    connection: sqlite3.Connection,
) -> None:
    first = create_user(connection)
    second = create_user(connection)
    save_profile(connection, _profile(first), actor="student:1")
    save_profile(connection, _profile(second), actor="student:2")
    fact_id = add_profile_fact(
        connection, user_id=first, field_name="coursework", value="Algorithms", source="document:1"
    )

    assert set_fact_status(connection, fact_id=fact_id, user_id=second, status="confirmed") is False
    assert set_fact_status(connection, fact_id=fact_id, user_id=first, status="confirmed") is True

    facts = confirmed_facts(connection, first)
    assert [fact["value"] for fact in facts] == ["Algorithms"]
    assert confirmed_facts(connection, second) == ()


def test_adding_the_same_fact_twice_is_idempotent(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")

    first = add_profile_fact(
        connection, user_id=user_id, field_name="gpa", value="8.9", source="document:1"
    )
    second = add_profile_fact(
        connection, user_id=user_id, field_name="gpa", value="8.9", source="document:1"
    )

    assert first == second
    assert len(list_profile_facts(connection, user_id)) == 1


@pytest.mark.parametrize("status", ["done", "PENDING", ""])
def test_invalid_fact_status_is_rejected(connection: sqlite3.Connection, status: str) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")

    with pytest.raises(ProfileError, match="status"):
        add_profile_fact(
            connection, user_id=user_id, field_name="gpa", value="8.9", source="d", status=status
        )


def test_fact_confidence_must_be_a_probability(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    save_profile(connection, _profile(user_id), actor="student:1")

    with pytest.raises(ProfileError, match="confidence"):
        add_profile_fact(
            connection,
            user_id=user_id,
            field_name="gpa",
            value="8.9",
            source="d",
            confidence=1.5,
        )


def test_set_fact_status_rejects_unknown_status(connection: sqlite3.Connection) -> None:
    with pytest.raises(ProfileError, match="confirmed or rejected"):
        set_fact_status(connection, fact_id=1, user_id=1, status="pending")
