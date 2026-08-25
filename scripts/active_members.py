"""How many members are on risk at a given date, and on what.

Run from the folder holding underwriting.db:

    python scripts/active_members.py                # at 2026-09-30
    python scripts/active_members.py 2026-12-31     # at any other date

Headcount on file and headcount on risk are different numbers. A
membership export carries members who have already left and members who
have not started yet, and only the ones on risk at the date you care
about cost anything.

The date matters more than it looks. Asking "who is active today" and
"who is still active at the end of the policy period" give different
answers on the same file - a member whose cover ends on 15 September is
active today and not active at 30 September. The second is the question
worth asking when pricing a period, so it is the default.
"""
import datetime
import sqlite3
import sys

#: Members whose cover ends before this are not on risk for the period
#: being looked at.
DEFAULT_AS_AT = "2026-09-30"

DB = "underwriting.db"
as_at = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AS_AT
datetime.date.fromisoformat(as_at)  # fail loudly on a bad date, not silently

c = sqlite3.connect(DB)


def q(sql, *args):
    return c.execute(sql, args).fetchall()


ON_RISK = """(member_end_date is null or member_end_date >= ?)
         and (member_start_date is null or member_start_date <= ?)"""

print(f"Members on risk at {as_at}\n")

total = q("select count(*) from portfolio_members")[0][0]
active = q(f"select count(*) from portfolio_members where {ON_RISK}", as_at, as_at)[0][0]
ended = q("""select count(*) from portfolio_members
             where member_end_date is not null and member_end_date < ?""", as_at)[0][0]
not_started = q("""select count(*) from portfolio_members
                   where member_start_date is not null and member_start_date > ?""", as_at)[0][0]
no_dates = q("""select count(*) from portfolio_members
                where member_end_date is null and member_start_date is null""")[0][0]

print(f"  on file                      {total:>8,}")
print(f"  ON RISK at {as_at}      {active:>8,}")
print(f"  cover ended before that date {ended:>8,}")
print(f"  not started by that date     {not_started:>8,}")
if no_dates:
    # Counted as on risk above. Worth seeing rather than assuming.
    print(f"  (of which no dates at all    {no_dates:>8,} - counted as on risk)")

for label, column in [("product", "product_name"), ("network", "network_type_raw"),
                      ("client", "contract")]:
    print(f"\nOn risk by {label}")
    rows = q(f"""select coalesce(nullif({column},''),'(not stated)'), count(*)
                 from portfolio_members where {ON_RISK}
                 group by 1 order by 2 desc limit 15""", as_at, as_at)
    for name, n in rows:
        print(f"  {str(name)[:42]:<44}{n:>7,}")

try:
    lines, patients = q("select count(*), count(distinct patient_id) from claims_ledger_entries")[0]
    if lines:
        print(f"\nServicePlan claims ledger: {lines:,} claim lines, {patients:,} distinct members")
except sqlite3.OperationalError:
    pass

c.close()
