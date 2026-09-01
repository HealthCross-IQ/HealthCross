"""Build the book once per data version, not once per endpoint.

Pricing one member is cheap; pricing the whole book is not. Every screen
that needs a burning-cost rate re-prices all 3,700 members and re-builds
the six-dimension cube from scratch, and the underwriting report does it
twice inside a single request. A print that touches six endpoints
therefore paid for the same arithmetic five or six times over, which is
what made the report take long enough for the browser to decide the tab
it was about to open could not have come from a click.

Nothing here changes an answer. The analysis is a pure function of the
uploaded data plus the arguments it was called with, so the same inputs
are guaranteed to produce the same output - the only question is whether
we notice that in time to skip the work.

Freshness comes from a data version rather than a clock, because the
failure mode worth avoiding is not a slow page - it is a wrong price.

The version is built from two independent signals, because neither is
sufficient on its own:

A write counter, bumped by a database-level listener whenever any
statement modifies one of the tables the analysis reads. This is the
reliable half, and it is a listener rather than a call at the top of
each upload endpoint precisely so it cannot be forgotten by the next
person to add one.

Row counts and highest ids, which additionally notice a write made by
something this process never saw - another process, or a file edited
directly. This half alone is not enough: SQLite reuses row ids after a
delete-all, so re-uploading a membership file with the same number of
rows lands back on the same ids, and a cache trusting counts would serve
the old book's burning cost against the new book's members. That is the
exact shape of every upload here, which is why the counter exists.
"""
import threading
from collections import OrderedDict
from typing import Callable, TypeVar

from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Delete, Insert, Update

from app.models import db_models as models

T = TypeVar("T")

#: The tables an analysis run reads. A change to any of them changes the
#: answer; a change to anything else does not.
_SOURCE_TABLES = (
    models.PortfolioMember,
    models.PortfolioClaimEntry,
    models.RateCard,
    models.BenefitVariantRate,
    models.GroupProductMapping,
    models.SubgroupMasterMapping,
)

#: Small on purpose. The point is to collapse the handful of calls made
#: within one report or one screen, not to hold the whole query space -
#: a full analysis is a few megabytes of dicts, and an unbounded cache
#: of them is a memory leak wearing a performance improvement's clothes.
MAX_ENTRIES = 8

_SOURCE_TABLE_NAMES = frozenset(table.__table__.name for table in _SOURCE_TABLES)

_lock = threading.Lock()
_entries: "OrderedDict[tuple, object]" = OrderedDict()
_hits = 0
_misses = 0
_writes = 0


@event.listens_for(Engine, "after_execute")
def _note_write_to_source_data(conn, clauseelement, multiparams, params, execution_options, result):
    """Count every statement that changes what an analysis would say.

    Listening at the engine catches the lot - ORM flushes, bulk inserts,
    and the delete-all that starts each upload - without every upload
    endpoint having to remember to announce itself. Reads outnumber
    writes here by orders of magnitude and never reach the counter, so
    this costs one isinstance check on the way past.
    """
    global _writes
    if isinstance(clauseelement, (Insert, Update, Delete)):
        table = getattr(clauseelement, "table", None)
        if table is not None and table.name in _SOURCE_TABLE_NAMES:
            with _lock:
                _writes += 1


def data_version(db: Session) -> tuple:
    """What the uploaded data currently is, cheaply enough to ask on
    every request.

    Six count/max aggregates against indexed primary keys, which SQLite
    answers without touching the rows themselves - far less work than
    the analysis it is deciding whether to skip.

    The database's own identity leads the version because row counts are
    only unique within one database. Two databases holding one member
    each both read as (1, 1), and a cache that cannot tell them apart
    serves one book's experience as the other's - which is how this was
    first caught, one test's member answering another test's question.
    """
    with _lock:
        writes = _writes
    return (str(db.get_bind().url), writes) + tuple(
        db.query(func.count(table.id), func.max(table.id)).one()
        for table in _SOURCE_TABLES
    )


def cached(db: Session, key: tuple, build: Callable[[], T]) -> T:
    """`build()`'s result, computed once per (data version, key).

    `key` must name every argument that changes the answer. Getting that
    wrong hands one caller another caller's numbers, so callers pass the
    whole argument tuple rather than the part that looks significant.

    The result is shared, not copied: callers treat an analysis as
    read-only today, and copying a few megabytes of dicts on every hit
    would give back most of what the cache just saved.
    """
    global _hits, _misses
    full_key = (data_version(db),) + key
    with _lock:
        if full_key in _entries:
            _entries.move_to_end(full_key)
            _hits += 1
            return _entries[full_key]  # type: ignore[return-value]
        _misses += 1

    # Built outside the lock: two requests arriving together may both
    # build, which wastes a little work once, where holding the lock
    # across a multi-second build would serialise every request in the
    # process behind it.
    value = build()

    with _lock:
        _entries[full_key] = value
        _entries.move_to_end(full_key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return value


def invalidate() -> None:
    """Drop everything. The data version already covers uploads; this is
    for tests, and for any future edit-in-place that would not move a
    row count or a maximum id.
    """
    with _lock:
        _entries.clear()


def stats() -> dict:
    with _lock:
        return {"entries": len(_entries), "hits": _hits, "misses": _misses}
