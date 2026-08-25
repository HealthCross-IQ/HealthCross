"""How many members are on risk, and on what.

Run from the folder holding underwriting.db:
    python active_members.py
"""
import datetime
import sqlite3

DB = "underwriting.db"
today = datetime.date.today().isoformat()
c = sqlite3.connect(DB)

def q(sql, *args):
    return c.execute(sql, args).fetchall()

print(f"As at {today}\n")

total = q("select count(*) from portfolio_members")[0][0]
active = q("""select count(*) from portfolio_members
              where (member_start_date is null or member_start_date <= ?)
                and (member_end_date   is null or member_end_date   >= ?)""", today, today)[0][0]
print(f"Portfolio members on file : {total:,}")
print(f"  active today            : {active:,}")
print(f"  lapsed / not yet started: {total - active:,}\n")

print("Active by product")
for product, n in q("""select coalesce(nullif(product_name,''),'(not stated)') , count(*)
                       from portfolio_members
                       where (member_start_date is null or member_start_date <= ?)
                         and (member_end_date   is null or member_end_date   >= ?)
                       group by 1 order by 2 desc""", today, today):
    print(f"  {product:<38} {n:>7,}")

print("\nActive by network")
for net, n in q("""select coalesce(nullif(network_type_raw,''),'(not stated)'), count(*)
                   from portfolio_members
                   where (member_start_date is null or member_start_date <= ?)
                     and (member_end_date   is null or member_end_date   >= ?)
                   group by 1 order by 2 desc limit 15""", today, today):
    print(f"  {net:<38} {n:>7,}")

# The "ServicePlan" claims ledger, if one has been uploaded.
try:
    rows = q("""select count(*), count(distinct patient_id) from claims_ledger_entries""")
    lines, patients = rows[0]
    if lines:
        print(f"\nServicePlan claims ledger : {lines:,} claim lines, "
              f"{patients:,} distinct members")
except sqlite3.OperationalError:
    pass
c.close()
