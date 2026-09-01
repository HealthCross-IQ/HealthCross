"""Skipping work is only an improvement while the answer stays right -
app/book/cache.py.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.book import cache as analysis_cache
from app.database import Base
from app.models import db_models as models


def _session(tmp_path, name="cache.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def _add_member(db, beneficiary_id):
    db.add(models.PortfolioMember(beneficiary_id=beneficiary_id, contract="Acme"))
    db.commit()


def test_the_same_question_is_only_answered_once(tmp_path):
    analysis_cache.invalidate()
    db = _session(tmp_path)
    calls = []

    def build():
        calls.append(1)
        return {"answer": 42}

    first = analysis_cache.cached(db, ("q",), build)
    second = analysis_cache.cached(db, ("q",), build)
    assert first == second == {"answer": 42}
    assert len(calls) == 1


def test_a_different_question_gets_its_own_answer(tmp_path):
    # The key names the arguments, so two callers asking different things
    # must not be handed each other's numbers.
    analysis_cache.invalidate()
    db = _session(tmp_path)
    assert analysis_cache.cached(db, ("a",), lambda: "A") == "A"
    assert analysis_cache.cached(db, ("b",), lambda: "B") == "B"


def test_an_upload_makes_the_cached_answer_stale(tmp_path):
    # The whole point. A burning cost computed before an upload is not a
    # slow answer, it is a wrong price.
    analysis_cache.invalidate()
    db = _session(tmp_path)
    assert analysis_cache.cached(db, ("q",), lambda: "before") == "before"
    _add_member(db, "B1")
    assert analysis_cache.cached(db, ("q",), lambda: "after") == "after"


def test_a_wholesale_replace_is_noticed_even_at_the_same_row_count(tmp_path):
    # Uploads delete every row and insert new ones, so a re-upload of the
    # same number of members leaves the count unchanged - it is the ids
    # moving that gives it away.
    analysis_cache.invalidate()
    db = _session(tmp_path)
    _add_member(db, "B1")
    assert analysis_cache.cached(db, ("q",), lambda: "first upload") == "first upload"

    db.query(models.PortfolioMember).delete()
    _add_member(db, "B2")
    assert analysis_cache.cached(db, ("q",), lambda: "second upload") == "second upload"


def test_two_databases_holding_one_member_each_are_not_the_same_database(tmp_path):
    # Row counts are only unique within one database. Without the
    # database's own identity in the version, one book's experience
    # answers the other book's question.
    analysis_cache.invalidate()
    left = _session(tmp_path, "left.db")
    right = _session(tmp_path, "right.db")
    _add_member(left, "L1")
    _add_member(right, "R1")
    assert analysis_cache.cached(left, ("q",), lambda: "left") == "left"
    assert analysis_cache.cached(right, ("q",), lambda: "right") == "right"


def test_a_failed_build_is_not_remembered_as_an_answer(tmp_path):
    # An analysis that raised because nothing was uploaded yet must not
    # keep raising after the upload arrives.
    analysis_cache.invalidate()
    db = _session(tmp_path)

    def boom():
        raise ValueError("nothing uploaded yet")

    try:
        analysis_cache.cached(db, ("q",), boom)
    except ValueError:
        pass
    assert analysis_cache.cached(db, ("q",), lambda: "now it works") == "now it works"


def test_the_cache_does_not_grow_without_bound(tmp_path):
    analysis_cache.invalidate()
    db = _session(tmp_path)
    for i in range(analysis_cache.MAX_ENTRIES + 5):
        analysis_cache.cached(db, (f"q{i}",), lambda i=i: i)
    assert analysis_cache.stats()["entries"] == analysis_cache.MAX_ENTRIES


def test_the_oldest_question_is_the_one_dropped(tmp_path):
    analysis_cache.invalidate()
    db = _session(tmp_path)
    analysis_cache.cached(db, ("keep",), lambda: "kept")
    for i in range(analysis_cache.MAX_ENTRIES):
        analysis_cache.cached(db, (f"filler{i}",), lambda: "filler")
        # Touching it keeps it recent, which is what "least recently
        # used" has to mean for it to survive a burst of other work.
        analysis_cache.cached(db, ("keep",), lambda: "rebuilt")
    assert analysis_cache.cached(db, ("keep",), lambda: "rebuilt") == "kept"
