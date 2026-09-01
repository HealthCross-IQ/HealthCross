"""The dependency rule, enforced.

An architectural boundary that is not tested is a suggestion, and this
one has already been crossed once: the book's data access lived inside
the Portfolio Analysis ROUTER, and six other modules reached in for its
private helpers - fourteen of those sixteen imports written inside a
function body, which is what people write to dodge the circular import a
module-level one would have caused. The function-local imports were the
code saying the layering was wrong.

The rule is that dependencies point one way:

    book  <-  casework  <-  renewal
                        <-  newbusiness

The book knows about members, claims, premiums and dates. It does not
know what a Case is, what a quote is, or what a renewal is. These tests
fail the moment that stops being true, which is the point - the next
person to reach for a book helper should find it in app/book, and the
person after that should not be able to reach back the other way.
"""
import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BOOK = REPO_ROOT / "app" / "book"


def _imported_modules(path: pathlib.Path):
    """Every module this file imports, at any indentation - a
    function-local import counts exactly like a top-level one."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_book_never_imports_a_workflow():
    # The one rule that keeps New Business and Renewal from bleeding into
    # each other: neither owns the book, so the book cannot know either.
    offenders = []
    for path in BOOK.glob("*.py"):
        for module in _imported_modules(path):
            if module.startswith("app.api") or ".routes_" in module:
                offenders.append(f"{path.name} imports {module}")
    assert not offenders, (
        "app/book must not depend on a router or a workflow:\n  " + "\n  ".join(offenders))


def test_the_book_does_not_know_what_a_case_is():
    # Case, CensusRecord, NewBusinessQuote and the rest are workflow
    # tables. The book is members, claims, mappings and the snapshot.
    workflow_tables = ("models.Case", "models.CensusRecord", "models.NewBusinessQuote",
                       "models.ClaimsLedgerEntry", "models.BenefitPlan", "models.Scorecard")
    offenders = [
        f"{path.name} references {table}"
        for path in BOOK.glob("*.py")
        for table in workflow_tables
        if table in path.read_text()
    ]
    assert not offenders, (
        "app/book must not read a workflow's tables:\n  " + "\n  ".join(offenders))


def test_nothing_reaches_into_the_portfolio_router_for_the_books_data():
    # This is the regression the whole extraction exists to prevent. A
    # router is a screen's API; it is not the data layer, and importing
    # out of one is what forced fourteen function-local imports.
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        if path.name == "routes_portfolio_analysis.py":
            continue
        for module in _imported_modules(path):
            if module.endswith("routes_portfolio_analysis"):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    assert not offenders, (
        "import the book from app.book, not out of a router:\n  " + "\n  ".join(offenders))


def test_the_book_is_read_through_one_reader_per_table():
    # The rate card reader existed three times over - once in the
    # portfolio router and again, byte-identical, in New Business - and
    # _subgroup_master_by_name was defined twice in the same file, the
    # second copy silently shadowing the first. Nobody notices until a
    # column is added to one reader and not the others.
    # The BOOK's own tables only. RateCard and BenefitVariantRate are New
    # Business's tables - New Business uploads and owns them, and the book
    # merely reads them to price members against - so New Business
    # querying its own rate card is not a boundary crossing.
    queried = {
        "models.PortfolioMember": [],
        "models.PortfolioClaimEntry": [],
        "models.SubgroupMasterMapping": [],
        "models.GroupProductMapping": [],
        "models.PortfolioDataSnapshot": [],
    }
    for path in (REPO_ROOT / "app").rglob("*.py"):
        text = path.read_text()
        for table in queried:
            if f"db.query({table}" in text:
                queried[table].append(str(path.relative_to(REPO_ROOT)))

    outside = {
        table: [f for f in files if not f.startswith("app/book/")]
        for table, files in queried.items()
    }
    # routes_portfolio_analysis still queries some of these directly for
    # its own screens; what must never come back is a SECOND general
    # reader in a workflow module.
    workflows = {
        table: [f for f in files
                if "routes_new_business_rating" in f or "routes_analysis" in f
                or "routes_cases" in f or "routes_chat" in f]
        for table, files in outside.items()
    }
    offenders = [f"{t}: {', '.join(f)}" for t, f in workflows.items() if f]
    assert not offenders, (
        "a workflow is reading a book table directly instead of via app/book/repository:\n  "
        + "\n  ".join(offenders))
