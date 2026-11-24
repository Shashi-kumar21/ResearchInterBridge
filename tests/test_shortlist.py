from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from research_inter_bridge import db, discovery, profiles
from research_inter_bridge import faculty as fac
from research_inter_bridge import shortlist as sl
from research_inter_bridge.auth import export_account_data, register
from research_inter_bridge.errors import (
    FacultyNotFoundError,
    ProfileNotFoundError,
    ProgramNotFoundError,
    ShortlistError,
    ShortlistItemNotFoundError,
)
from research_inter_bridge.profiles import DegreeType, StudentProfile

SEEDED = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 24)
PROFILE_URL = "https://www.cs.example.edu/people/jane"
OTHER_URL = "https://www.cs.example.edu/people/ravi"


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@dataclass
class FacultyAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


@dataclass
class ProgramAdapter:
    records: tuple[discovery.DiscoveredProgram, ...]
    name: str = "fixture"
    parser_version: str = "1"

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        return self.records


def person(name: str, url: str, interests: tuple[str, ...]) -> fac.DiscoveredFaculty:
    return fac.DiscoveredFaculty(
        full_name=name,
        institution="Example University",
        country="Germany",
        department="Computer Science",
        profile_url=url,
        research_interests=interests,
    )


def seed(connection: sqlite3.Connection) -> dict[str, int]:
    faculty = fac.search_faculty(
        connection,
        fac.FacultyQuery(),
        adapter=FacultyAdapter(
            records=(
                person("Jane Doe", PROFILE_URL, ("machine learning", "proteins")),
                person("Ravi Kumar", OTHER_URL, ("compilers",)),
            )
        ),
        now=SEEDED,
    ).faculty
    programs = discovery.search_programs(
        connection,
        discovery.ProgramQuery(),
        adapter=ProgramAdapter(
            records=(
                discovery.DiscoveredProgram(
                    name="PhD Robotics",
                    institution="Example University",
                    country="Germany",
                    degree_level="PhD",
                    official_url="https://example.edu/phd-robotics",
                    research_areas=("robotics",),
                    status="open",
                ),
            )
        ),
        now=SEEDED,
    ).programs
    ids = {member.full_name: member.id for member in faculty}
    ids["PhD Robotics"] = programs[0].id
    return ids


def student(
    connection: sqlite3.Connection, email: str = "asha@example.edu", **fields: object
) -> int:
    account = register(
        connection,
        email=email,
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    )
    values: dict[str, object] = {
        "user_id": account.user_id,
        "full_name": "Asha Rao",
        "contact_email": email,
        "degree_type": DegreeType.MSC,
        "research_keywords": ("machine learning",),
        "target_countries": ("Germany",),
    }
    values.update(fields)
    profiles.save_profile(connection, StudentProfile(**values), actor="test")  # type: ignore[arg-type]
    return account.user_id


def fac_target(ids: dict[str, int], name: str) -> sl.Target:
    return sl.Target("faculty", ids[name])


def test_multi_select_adds_and_snapshots_each_target(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)

    result = sl.add(
        connection,
        user,
        [
            fac_target(ids, "Jane Doe"),
            fac_target(ids, "Ravi Kumar"),
            sl.Target("program", ids["PhD Robotics"]),
        ],
        today=TODAY,
    )
    entries = sl.list_items(connection, user, today=TODAY)

    assert len(result.changed) == 3
    assert [entry.target_name for entry in entries] == ["Jane Doe", "Ravi Kumar", "PhD Robotics"]
    jane, ravi, robotics = entries
    assert jane.snapshot is not None and jane.snapshot.score > ravi.snapshot.score  # type: ignore[union-attr]
    assert jane.current is True
    assert robotics.snapshot is not None
    assert robotics.snapshot.eligibility == "ineligible"  # PhD program, master student
    assert {factor["key"] for factor in jane.snapshot.result["factors"]} >= {"research_interest"}  # type: ignore[union-attr]


def test_shortlisting_twice_is_deduplicated(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)
    target = fac_target(ids, "Jane Doe")

    sl.add(connection, user, [target, target], today=TODAY)
    again = sl.add(connection, user, [target], today=TODAY)

    assert again.changed == ()
    assert again.unchanged == (target,)
    rows = connection.execute("SELECT COUNT(*) FROM shortlist_items").fetchone()[0]
    snapshots = connection.execute("SELECT COUNT(*) FROM match_scores").fetchone()[0]
    assert (rows, snapshots) == (1, 1)


def test_an_unknown_target_adds_nothing(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)

    with pytest.raises(FacultyNotFoundError):
        sl.add(
            connection, user, [fac_target(ids, "Jane Doe"), sl.Target("faculty", 9999)], today=TODAY
        )
    with pytest.raises(ProgramNotFoundError):
        sl.exclude(connection, user, [sl.Target("program", 9999)])

    assert connection.execute("SELECT COUNT(*) FROM shortlist_items").fetchone()[0] == 0


def test_shortlisting_needs_a_profile(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    account = register(
        connection,
        email="noprofile@example.edu",
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    )

    with pytest.raises(ProfileNotFoundError):
        sl.add(connection, account.user_id, [fac_target(ids, "Jane Doe")], today=TODAY)


def test_exclude_then_shortlist_moves_the_row(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)
    target = fac_target(ids, "Ravi Kumar")

    sl.exclude(connection, user, [target])
    assert sl.excluded_targets(connection, user) == {target}
    assert sl.list_items(connection, user, status="excluded", today=TODAY)[0].snapshot is None

    sl.add(connection, user, [target], today=TODAY)

    assert sl.excluded_targets(connection, user) == frozenset()
    (entry,) = sl.list_items(connection, user, today=TODAY)
    assert entry.status == "shortlisted"
    assert entry.snapshot is not None


def test_remove_forgets_the_row_and_its_snapshots(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)
    target = fac_target(ids, "Jane Doe")
    sl.add(connection, user, [target], today=TODAY)

    sl.remove(connection, user, target)

    assert sl.list_items(connection, user, today=TODAY) == ()
    assert connection.execute("SELECT COUNT(*) FROM match_scores").fetchone()[0] == 0
    with pytest.raises(ShortlistItemNotFoundError):
        sl.remove(connection, user, target)


def test_a_profile_change_marks_the_snapshot_stale_and_refresh_stores_one(
    connection: sqlite3.Connection,
) -> None:
    ids = seed(connection)
    user = student(connection)
    sl.add(connection, user, [fac_target(ids, "Ravi Kumar")], today=TODAY)
    before = sl.list_items(connection, user, today=TODAY)[0].snapshot

    profile = profiles.load_profile(connection, user)
    profiles.save_profile(
        connection,
        StudentProfile(**{**_fields(profile), "research_keywords": ("compilers",)}),  # type: ignore[arg-type]
        actor="test",
    )
    stale = sl.list_items(connection, user, today=TODAY)[0]
    assert stale.current is False

    first = sl.refresh(connection, user, today=TODAY)
    second = sl.refresh(connection, user, today=TODAY)

    fresh = sl.list_items(connection, user, today=TODAY)[0]
    assert len(first.changed) == 1
    assert second.changed == ()
    assert fresh.current is True
    assert fresh.snapshot is not None and before is not None
    assert fresh.snapshot.score > before.score
    assert connection.execute("SELECT COUNT(*) FROM match_scores").fetchone()[0] == 2


def test_students_never_see_each_others_rows(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    asha = student(connection)
    ben = student(connection, email="ben@example.edu", full_name="Ben Ode")
    target = fac_target(ids, "Jane Doe")

    sl.add(connection, asha, [target], today=TODAY)

    assert sl.list_items(connection, ben, today=TODAY) == ()
    with pytest.raises(ShortlistItemNotFoundError):
        sl.remove(connection, ben, target)
    # Ben adding the same target creates his own row, leaving Asha's untouched.
    sl.add(connection, ben, [target], today=TODAY)
    assert len(sl.list_items(connection, asha, today=TODAY)) == 1
    assert connection.execute("SELECT COUNT(*) FROM shortlist_items").fetchone()[0] == 2


def test_deleting_the_account_removes_shortlist_and_snapshots(
    connection: sqlite3.Connection,
) -> None:
    ids = seed(connection)
    user = student(connection)
    sl.add(connection, user, [fac_target(ids, "Jane Doe")], today=TODAY)

    profiles.delete_user(connection, user, actor="test")

    assert connection.execute("SELECT COUNT(*) FROM shortlist_items").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM match_scores").fetchone()[0] == 0


def test_the_export_includes_the_shortlist(connection: sqlite3.Connection) -> None:
    ids = seed(connection)
    user = student(connection)
    sl.add(connection, user, [fac_target(ids, "Jane Doe")], today=TODAY)

    exported = export_account_data(connection, user)

    (item,) = exported["shortlist"]
    assert item["target_type"] == "faculty"
    assert item["status"] == "shortlisted"
    assert isinstance(item["score"], int)


def test_audit_rows_carry_ids_and_counts_but_no_profile_text(
    connection: sqlite3.Connection,
) -> None:
    ids = seed(connection)
    user = student(connection)
    sl.add(connection, user, [fac_target(ids, "Jane Doe")], today=TODAY)

    row = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'shortlist.add'"
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata == {"changed": [f"faculty:{ids['Jane Doe']}"], "unchanged": 0}
    assert "machine learning" not in row["metadata_json"]


@pytest.mark.parametrize(
    ("make", "fragment"),
    [
        (lambda: sl.Target("lab", 1), "not supported"),
        (lambda: sl.Target("faculty", 0), "positive integer"),
    ],
)
def test_invalid_targets_are_rejected(make, fragment: str) -> None:
    with pytest.raises(ShortlistError, match=fragment):
        make()


def test_batch_and_capacity_limits(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed(connection)
    user = student(connection)

    with pytest.raises(ShortlistError, match="at least one"):
        sl.add(connection, user, [], today=TODAY)
    monkeypatch.setattr(sl, "MAX_BATCH", 1)
    with pytest.raises(ShortlistError, match="at most 1"):
        sl.add(connection, user, [fac_target(ids, "Jane Doe"), fac_target(ids, "Ravi Kumar")])
    monkeypatch.setattr(sl, "MAX_BATCH", 50)
    monkeypatch.setattr(sl, "MAX_ITEMS", 1)
    sl.add(connection, user, [fac_target(ids, "Jane Doe")], today=TODAY)
    with pytest.raises(ShortlistError) as excinfo:
        sl.exclude(connection, user, [fac_target(ids, "Ravi Kumar")])
    assert excinfo.value.code == "shortlist_full"


def _fields(profile: StudentProfile) -> dict[str, object]:
    return {
        name: getattr(profile, name)
        for name in StudentProfile.__dataclass_fields__
        if name not in ("created_at", "updated_at")
    }
