from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.finance.cash_flow import forecast_expenses, monthly_cash_flow
from app.finance.common import normalize_doc_no
from app.finance.fee_engine import FeeRate, compute_hc_fee
from app.finance.reconciliation import (
    compare_qic_soa_periods,
    reconcile_tracker_received_vs_bank,
    reconcile_tracker_vs_qic_soa,
)
from app.ingestion.bank_statement import parse_bank_statement
from app.ingestion.payment_tracker import parse_payment_tracker
from app.ingestion.qic_soa import parse_qic_soa
from app.models import db_models as models
from app.models import schemas

router = APIRouter(prefix="/finance", tags=["finance"])


def _to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _active_fee_rates(db: Session) -> List[FeeRate]:
    rows = db.query(models.FeeRateCard).filter_by(is_active=True).all()
    return [FeeRate(channel=r.channel, tier_band=r.tier_band, fee_pct=r.fee_pct) for r in rows]


def _get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return obj


# ---------------------------------------------------------------------------
# Fee rate cards
# ---------------------------------------------------------------------------


@router.post("/fee-rate-cards", response_model=schemas.FeeRateCardOut)
def create_fee_rate_card(payload: schemas.FeeRateCardCreate, db: Session = Depends(get_db)):
    card = models.FeeRateCard(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.get("/fee-rate-cards", response_model=List[schemas.FeeRateCardOut])
def list_fee_rate_cards(active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(models.FeeRateCard)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(models.FeeRateCard.channel, models.FeeRateCard.tier_band).all()


# ---------------------------------------------------------------------------
# Payment tracker
# ---------------------------------------------------------------------------


def _get_tracker_entry_or_404(db: Session, entry_id: int) -> models.PaymentTrackerEntry:
    return _get_or_404(db, models.PaymentTrackerEntry, entry_id, "Payment tracker entry")


@router.post("/payment-tracker", response_model=schemas.PaymentTrackerEntryOut)
def create_payment_tracker_entry(payload: schemas.PaymentTrackerEntryCreate, db: Session = Depends(get_db)):
    """Creates one payment tracker entry going forward, computing HC's fee
    via app.finance.fee_engine (rate-card lookup, or the supplied
    manual_fee_pct for a Group/mixed-tier row) - distinct from
    /payment-tracker/upload, which imports historical rows with their
    already-computed fee figures preserved as-is.
    """
    try:
        fee = compute_hc_fee(
            channel=payload.channel,
            product=payload.product,
            premium_excl_vat=payload.premium_excl_vat,
            rate_cards=_active_fee_rates(db),
            manual_fee_pct=payload.manual_fee_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    data = payload.model_dump(exclude={"manual_fee_pct", "doc_no"})
    entry = models.PaymentTrackerEntry(
        **data,
        doc_no=normalize_doc_no(payload.doc_no),
        doc_no_raw=payload.doc_no,
        is_manual_fee=fee["is_manual_fee"],
        hc_fee_pct=fee["hc_fee_pct"],
        hc_fees=fee["hc_fees"],
        vat_pct=fee["vat_pct"],
        vat_amount=fee["vat_amount"],
        total_value=fee["total_value"],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/payment-tracker", response_model=List[schemas.PaymentTrackerEntryOut])
def list_payment_tracker_entries(
    channel: Optional[str] = None,
    hc_payment_status: Optional[str] = None,
    client_payment_status: Optional[str] = None,
    doc_no: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.PaymentTrackerEntry)
    if channel:
        q = q.filter_by(channel=channel)
    if hc_payment_status:
        q = q.filter_by(hc_payment_status=hc_payment_status)
    if client_payment_status:
        q = q.filter_by(client_payment_status=client_payment_status)
    if doc_no:
        q = q.filter_by(doc_no=normalize_doc_no(doc_no))
    return q.order_by(models.PaymentTrackerEntry.doc_date.desc()).all()


@router.get("/payment-tracker/{entry_id}", response_model=schemas.PaymentTrackerEntryOut)
def get_payment_tracker_entry(entry_id: int, db: Session = Depends(get_db)):
    return _get_tracker_entry_or_404(db, entry_id)


@router.patch("/payment-tracker/{entry_id}", response_model=schemas.PaymentTrackerEntryOut)
def update_payment_tracker_entry(entry_id: int, payload: schemas.PaymentTrackerEntryUpdate, db: Session = Depends(get_db)):
    entry = _get_tracker_entry_or_404(db, entry_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.post("/payment-tracker/upload", response_model=List[schemas.PaymentTrackerEntryOut])
def upload_payment_tracker(
    file: UploadFile = File(...),
    sheet_name: Optional[str] = Query(
        None, description="Workbook sheet to read (e.g. 'Payment Tracker', 'Ledgers', 'Eman'). Defaults to the first sheet."
    ),
    mode: str = Query(
        "append",
        description=(
            "'append' (default): add these rows to the existing tracker - the normal case, since "
            "each upload is usually a fresh period's rows. 'replace': wipe every existing payment "
            "tracker row first - use for a full bootstrap re-import of the master file."
        ),
    ),
    db: Session = Depends(get_db),
):
    try:
        parsed = parse_payment_tracker(file.file, file.filename, sheet_name=sheet_name or 0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse payment tracker file: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No payment tracker rows found in file")

    if mode == "replace":
        db.query(models.PaymentTrackerEntry).delete()

    entries = [models.PaymentTrackerEntry(source_batch=file.filename, **row) for row in parsed]
    db.add_all(entries)
    db.commit()
    for entry in entries:
        db.refresh(entry)
    return entries


# ---------------------------------------------------------------------------
# QIC Statement of Account
# ---------------------------------------------------------------------------


@router.get("/qic-soa", response_model=List[schemas.QicSoaLineOut])
def list_qic_soa_lines(statement_period: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.QicSoaLine)
    if statement_period:
        q = q.filter_by(statement_period=statement_period)
    return q.order_by(models.QicSoaLine.doc_date.desc()).all()


@router.post("/qic-soa/upload", response_model=List[schemas.QicSoaLineOut])
def upload_qic_soa(
    file: UploadFile = File(...),
    statement_period: str = Query(
        ...,
        description=(
            "A label for which SOA export this is (e.g. '2026-06', '2026-07-recon') - lets "
            "GET /finance/reconciliation/qic-periods compare two uploads, and re-uploading the SAME "
            "label replaces that period's rows rather than duplicating them."
        ),
    ),
    sheet_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        parsed = parse_qic_soa(file.file, file.filename, sheet_name=sheet_name or 0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse QIC SOA file: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No QIC SOA rows found in file")

    # Replace, not accumulate - re-uploading the same statement_period label
    # is a refresh of that period's export, not a second copy of it.
    db.query(models.QicSoaLine).filter_by(statement_period=statement_period).delete()

    lines = [models.QicSoaLine(statement_period=statement_period, **row) for row in parsed]
    db.add_all(lines)
    db.commit()
    for line in lines:
        db.refresh(line)
    return lines


# ---------------------------------------------------------------------------
# Bank statement
# ---------------------------------------------------------------------------


@router.get("/bank-transactions", response_model=List[schemas.BankTransactionOut])
def list_bank_transactions(statement_period: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.BankTransaction)
    if statement_period:
        q = q.filter_by(statement_period=statement_period)
    return q.order_by(models.BankTransaction.txn_date.desc()).all()


@router.post("/bank-statement/upload", response_model=List[schemas.BankTransactionOut])
def upload_bank_statement(
    file: UploadFile = File(...),
    statement_period: Optional[str] = Query(
        None, description="Optional label for this statement (e.g. '2026-07'). Re-uploading the SAME label replaces that period's rows."
    ),
    sheet_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        parsed = parse_bank_statement(file.file, file.filename, sheet_name=sheet_name or 0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse bank statement file: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No bank transactions found in file")

    if statement_period:
        db.query(models.BankTransaction).filter_by(statement_period=statement_period).delete()

    transactions = [models.BankTransaction(statement_period=statement_period, **row) for row in parsed]
    db.add_all(transactions)
    db.commit()
    for txn in transactions:
        db.refresh(txn)
    return transactions


# ---------------------------------------------------------------------------
# Reconciliation reports
# ---------------------------------------------------------------------------


@router.get("/reconciliation/tracker-vs-qic", response_model=schemas.TrackerQicReconciliationOut)
def get_tracker_vs_qic_reconciliation(statement_period: Optional[str] = None, db: Session = Depends(get_db)):
    tracker_entries = [_to_dict(e) for e in db.query(models.PaymentTrackerEntry).all()]
    qic_query = db.query(models.QicSoaLine)
    if statement_period:
        qic_query = qic_query.filter_by(statement_period=statement_period)
    qic_lines = [_to_dict(e) for e in qic_query.all()]
    return reconcile_tracker_vs_qic_soa(tracker_entries, qic_lines, statement_period=statement_period)


@router.get("/reconciliation/qic-periods", response_model=schemas.SoaPeriodComparisonOut)
def get_qic_period_comparison(period_a: str, period_b: str, db: Session = Depends(get_db)):
    lines_a = [_to_dict(e) for e in db.query(models.QicSoaLine).filter_by(statement_period=period_a).all()]
    lines_b = [_to_dict(e) for e in db.query(models.QicSoaLine).filter_by(statement_period=period_b).all()]
    if not lines_a:
        raise HTTPException(status_code=404, detail=f"No QIC SOA lines found for period {period_a!r}")
    if not lines_b:
        raise HTTPException(status_code=404, detail=f"No QIC SOA lines found for period {period_b!r}")
    return compare_qic_soa_periods(lines_a, lines_b, period_a, period_b)


@router.get("/reconciliation/tracker-vs-bank", response_model=schemas.BankReconciliationOut)
def get_tracker_vs_bank_reconciliation(db: Session = Depends(get_db)):
    tracker_entries = [_to_dict(e) for e in db.query(models.PaymentTrackerEntry).all()]
    bank_transactions = [_to_dict(e) for e in db.query(models.BankTransaction).all()]
    return reconcile_tracker_received_vs_bank(tracker_entries, bank_transactions)


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------


def _get_employee_or_404(db: Session, employee_id: int) -> models.Employee:
    return _get_or_404(db, models.Employee, employee_id, "Employee")


@router.post("/employees", response_model=schemas.EmployeeOut)
def create_employee(payload: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    employee = models.Employee(**payload.model_dump())
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@router.get("/employees", response_model=List[schemas.EmployeeOut])
def list_employees(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(models.Employee)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(models.Employee.full_name).all()


@router.patch("/employees/{employee_id}", response_model=schemas.EmployeeOut)
def update_employee(employee_id: int, payload: schemas.EmployeeUpdate, db: Session = Depends(get_db)):
    employee = _get_employee_or_404(db, employee_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(employee, field, value)
    db.commit()
    db.refresh(employee)
    return employee


@router.delete("/employees/{employee_id}", status_code=204)
def delete_employee(employee_id: int, db: Session = Depends(get_db)):
    employee = _get_employee_or_404(db, employee_id)
    # Keep past salary ExpenseEntry rows intact - just unlink them - rather
    # than deleting payroll history along with the roster entry.
    db.query(models.ExpenseEntry).filter_by(employee_id=employee_id).update({"employee_id": None})
    db.delete(employee)
    db.commit()


# ---------------------------------------------------------------------------
# Recurring expenses
# ---------------------------------------------------------------------------


def _get_recurring_expense_or_404(db: Session, recurring_expense_id: int) -> models.RecurringExpense:
    return _get_or_404(db, models.RecurringExpense, recurring_expense_id, "Recurring expense")


@router.post("/recurring-expenses", response_model=schemas.RecurringExpenseOut)
def create_recurring_expense(payload: schemas.RecurringExpenseCreate, db: Session = Depends(get_db)):
    recurring = models.RecurringExpense(**payload.model_dump())
    db.add(recurring)
    db.commit()
    db.refresh(recurring)
    return recurring


@router.get("/recurring-expenses", response_model=List[schemas.RecurringExpenseOut])
def list_recurring_expenses(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(models.RecurringExpense)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(models.RecurringExpense.name).all()


@router.patch("/recurring-expenses/{recurring_expense_id}", response_model=schemas.RecurringExpenseOut)
def update_recurring_expense(recurring_expense_id: int, payload: schemas.RecurringExpenseUpdate, db: Session = Depends(get_db)):
    recurring = _get_recurring_expense_or_404(db, recurring_expense_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(recurring, field, value)
    db.commit()
    db.refresh(recurring)
    return recurring


@router.delete("/recurring-expenses/{recurring_expense_id}", status_code=204)
def delete_recurring_expense(recurring_expense_id: int, db: Session = Depends(get_db)):
    recurring = _get_recurring_expense_or_404(db, recurring_expense_id)
    # Same rationale as employee deletion - keep past ExpenseEntry rows, just unlink them.
    db.query(models.ExpenseEntry).filter_by(recurring_expense_id=recurring_expense_id).update({"recurring_expense_id": None})
    db.delete(recurring)
    db.commit()


# ---------------------------------------------------------------------------
# Expense entries
# ---------------------------------------------------------------------------


def _get_expense_entry_or_404(db: Session, expense_id: int) -> models.ExpenseEntry:
    return _get_or_404(db, models.ExpenseEntry, expense_id, "Expense entry")


@router.post("/expenses", response_model=schemas.ExpenseEntryOut)
def create_expense_entry(payload: schemas.ExpenseEntryCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    data["period"] = date(data["period"].year, data["period"].month, 1)
    expense = models.ExpenseEntry(**data, source="manual")
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/expenses", response_model=List[schemas.ExpenseEntryOut])
def list_expense_entries(
    year: Optional[int] = None,
    category: Optional[str] = None,
    expense_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.ExpenseEntry)
    if year:
        q = q.filter(models.ExpenseEntry.period >= date(year, 1, 1), models.ExpenseEntry.period < date(year + 1, 1, 1))
    if category:
        q = q.filter_by(category=category)
    if expense_type:
        q = q.filter_by(expense_type=expense_type)
    return q.order_by(models.ExpenseEntry.period.desc()).all()


@router.patch("/expenses/{expense_id}", response_model=schemas.ExpenseEntryOut)
def update_expense_entry(expense_id: int, payload: schemas.ExpenseEntryUpdate, db: Session = Depends(get_db)):
    expense = _get_expense_entry_or_404(db, expense_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(expense, field, value)
    db.commit()
    db.refresh(expense)
    return expense


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense_entry(expense_id: int, db: Session = Depends(get_db)):
    expense = _get_expense_entry_or_404(db, expense_id)
    db.delete(expense)
    db.commit()


@router.post("/expenses/generate", response_model=List[schemas.ExpenseEntryOut])
def generate_expenses_for_period(
    period: date = Query(..., description="Any day within the target month - normalized to the 1st."),
    db: Session = Depends(get_db),
):
    """Auto-creates this month's salary (one per active Employee) and fixed
    RecurringExpense entries, skipping anyone/anything that already has an
    ExpenseEntry for this period - safe to call again after adding a new
    employee mid-month without duplicating the ones already generated.
    """
    period_start = date(period.year, period.month, 1)
    existing = db.query(models.ExpenseEntry).filter_by(period=period_start).all()
    existing_employee_ids = {e.employee_id for e in existing if e.employee_id}
    existing_recurring_ids = {e.recurring_expense_id for e in existing if e.recurring_expense_id}

    created = []
    for employee in db.query(models.Employee).filter_by(is_active=True).all():
        if employee.id in existing_employee_ids:
            continue
        created.append(
            models.ExpenseEntry(
                period=period_start,
                category="salary",
                expense_type="fixed",
                description=f"{employee.full_name} - {employee.role_title or 'Salary'}",
                amount=employee.monthly_salary,
                currency=employee.currency,
                employee_id=employee.id,
                source="generated",
            )
        )

    for recurring in db.query(models.RecurringExpense).filter_by(is_active=True, expense_type="fixed").all():
        if recurring.id in existing_recurring_ids or recurring.default_amount is None:
            continue
        created.append(
            models.ExpenseEntry(
                period=period_start,
                category=recurring.category,
                expense_type="fixed",
                description=recurring.name,
                amount=recurring.default_amount,
                currency=recurring.currency,
                recurring_expense_id=recurring.id,
                source="generated",
            )
        )

    db.add_all(created)
    db.commit()
    for expense in created:
        db.refresh(expense)
    return created


# ---------------------------------------------------------------------------
# Cash flow, forecast, and summary
# ---------------------------------------------------------------------------


@router.get("/cash-flow", response_model=schemas.CashFlowOut)
def get_cash_flow(year: int, db: Session = Depends(get_db)):
    tracker_entries = [_to_dict(e) for e in db.query(models.PaymentTrackerEntry).all()]
    expense_entries = [_to_dict(e) for e in db.query(models.ExpenseEntry).all()]
    return monthly_cash_flow(tracker_entries, expense_entries, year)


@router.get("/expense-forecast", response_model=schemas.ExpenseForecastOut)
def get_expense_forecast(year: int, as_of: Optional[date] = None, db: Session = Depends(get_db)):
    expense_entries = [_to_dict(e) for e in db.query(models.ExpenseEntry).all()]
    employees = [_to_dict(e) for e in db.query(models.Employee).all()]
    recurring_expenses = [_to_dict(e) for e in db.query(models.RecurringExpense).all()]
    return forecast_expenses(expense_entries, employees, recurring_expenses, year, as_of=as_of or date.today())


@router.get("/summary", response_model=schemas.FinanceSummaryOut)
def get_finance_summary(db: Session = Depends(get_db)):
    entries = db.query(models.PaymentTrackerEntry).all()
    total_invoiced = sum(e.total_value or 0 for e in entries)
    total_received = sum(
        e.total_value or 0 for e in entries if (e.hc_payment_status or "").strip().lower().startswith("received")
    )
    current_year = date.today().year
    ytd_expenses = sum(
        e.amount or 0
        for e in db.query(models.ExpenseEntry)
        .filter(models.ExpenseEntry.period >= date(current_year, 1, 1))
        .all()
    )
    return schemas.FinanceSummaryOut(
        total_hc_fees_invoiced=round(total_invoiced, 2),
        total_hc_fees_received=round(total_received, 2),
        total_outstanding=round(total_invoiced - total_received, 2),
        ytd_expenses=round(ytd_expenses, 2),
        ytd_net=round(total_received - ytd_expenses, 2),
        as_of=datetime.utcnow(),
    )
