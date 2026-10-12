%%writefile src/errors.py
"""Custom exception hierarchy for the tax engine."""

class TaxError(Exception):
    """Base class for every tax-engine error."""

class InvalidIncomeError(TaxError):
    """Income, revenue, allowance or deduction is negative or of the wrong type."""

class NoScheduleForDateError(TaxError):
    """No tax schedule is effective for the requested date/residency."""

class InvalidContextError(TaxError):
    """A tax was given a context type it does not understand."""

class InvalidInvoiceError(TaxError):
    """An invoice is malformed."""

##
%%writefile src/money.py
"""Money value object in UGX backed by decimal.Decimal."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import total_ordering
from typing import Union

Number = Union[int, str, Decimal]


@total_ordering
class Money:
    """Immutable UGX amount.

    Rounding rule: ROUND_HALF_UP to whole shillings, applied whenever a Money is created
    (so every operation result is rounded). Floats are rejected on purpose.
    """
    __slots__ = ("_amount",)
    CURRENCY = "UGX"
    ROUNDING = ROUND_HALF_UP

    def __init__(self, amount: Union[Number, "Money"] = 0) -> None:
        if isinstance(amount, Money):
            amount = amount._amount
        if isinstance(amount, (float, bool)):
            raise TypeError("Money does not accept float/bool; use int, str or Decimal")
        try:
            value = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise TypeError(f"Cannot create Money from {amount!r}") from exc
        if not value.is_finite():
            raise ValueError("Money must be finite")
        self._amount = value.quantize(Decimal("1"), rounding=self.ROUNDING)

    @classmethod
    def zero(cls) -> "Money":
        return cls(0)

    @property
    def amount(self) -> Decimal:
        return self._amount

    # arithmetic
    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount + other._amount)

    def __radd__(self, other: object) -> "Money":
        return self if other == 0 else NotImplemented  # lets sum() work

    def __sub__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount - other._amount)

    def __mul__(self, factor: object) -> "Money":
        if isinstance(factor, bool) or not isinstance(factor, (int, Decimal)):
            return NotImplemented  # floats and Money are rejected
        return Money(self._amount * Decimal(factor))

    __rmul__ = __mul__

    def __neg__(self) -> "Money":
        return Money(-self._amount)

    # comparison
    def __eq__(self, other: object) -> bool:
        return self._amount == other._amount if isinstance(other, Money) else NotImplemented

    def __lt__(self, other: object) -> bool:
        return self._amount < other._amount if isinstance(other, Money) else NotImplemented

    def __hash__(self) -> int:
        return hash(self._amount)

    def __bool__(self) -> bool:
        return self._amount != 0

    def __repr__(self) -> str:
        return f"Money('{self._amount}')"

    def __str__(self) -> str:
        return f"UGX {self._amount:,.0f}"

###
%%writefile src/base.py
"""Abstract Tax class, contexts and results."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

from .errors import InvalidContextError, InvalidIncomeError
from .money import Money


@dataclass(frozen=True)
class TaxContext:
    """Base context: the date on which the tax is assessed."""
    as_of: date

    def __post_init__(self) -> None:
        if not isinstance(self.as_of, date):
            raise InvalidContextError(f"as_of must be a date, got {type(self.as_of).__name__}")


def _check_non_negative(**values: Money) -> None:
    for label, v in values.items():
        if not isinstance(v, Money):
            raise InvalidIncomeError(f"{label} must be Money, got {type(v).__name__}")
        if v < Money.zero():
            raise InvalidIncomeError(f"{label} cannot be negative: {v}")


@dataclass(frozen=True)
class PayrollContext(TaxContext):
    """Monthly payroll inputs for one employee."""
    gross: Money = field(default_factory=Money.zero)
    allowances: Money = field(default_factory=Money.zero)
    resident: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        _check_non_negative(gross=self.gross, allowances=self.allowances)

    @property
    def total_emoluments(self) -> Money:
        """Gross pay plus (taxable) allowances."""
        return self.gross + self.allowances


@dataclass(frozen=True)
class CorporateContext(TaxContext):
    """Annual inputs for corporate income tax."""
    revenue: Money = field(default_factory=Money.zero)
    deductions: Money = field(default_factory=Money.zero)
    resident: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        _check_non_negative(revenue=self.revenue, deductions=self.deductions)


@dataclass(frozen=True)
class TaxResult:
    """Outcome of a tax calculation."""
    tax_name: str
    taxable_base: Money
    amount: Money
    breakdown: Mapping[str, Money] = field(default_factory=dict)
    notes: str = ""


class Tax(ABC):
    """Abstract base for every tax."""
    name: str = "Tax"

    @abstractmethod
    def calculate(self, ctx: TaxContext) -> TaxResult:
        """Compute the tax for the given context."""

    def _require(self, ctx: TaxContext, kind: type) -> None:
        if not isinstance(ctx, kind):
            raise InvalidContextError(f"{self.name} needs a {kind.__name__}, got {type(ctx).__name__}")

##
%%writefile src/payroll.py
"""PAYE (date-effective schedules), NSSF, Local Service Tax and the payroll pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .base import PayrollContext, Tax, TaxResult
from .errors import InvalidIncomeError, NoScheduleForDateError
from .money import Money


@dataclass(frozen=True)
class Band:
    """Marginal band: income in (lower, upper] is taxed at ``rate``. upper=None means no limit."""
    lower: Decimal
    upper: Optional[Decimal]
    rate: Decimal


@dataclass(frozen=True)
class PayeSchedule:
    """A monthly PAYE schedule valid for a date range and residency."""
    resident: bool
    effective_from: date
    effective_to: Optional[date]
    bands: tuple[Band, ...]
    surcharge_threshold: Decimal = Decimal("10000000")
    surcharge_rate: Decimal = Decimal("0.10")
    source: str = ""

    def applies(self, as_of: date, resident: bool) -> bool:
        return (self.resident == resident and self.effective_from <= as_of
                and (self.effective_to is None or as_of <= self.effective_to))

    def compute(self, income: Money) -> Money:
        """Progressive tax plus surcharge, rounded once at the end."""
        x = income.amount
        total = Decimal(0)
        for b in self.bands:
            if x <= b.lower:
                break
            top = x if b.upper is None else min(x, b.upper)
            total += (top - b.lower) * b.rate
        if x > self.surcharge_threshold:
            total += (x - self.surcharge_threshold) * self.surcharge_rate
        return Money(total)


D = Decimal
# NOTE: confirm bands, effective dates and the non-resident schedule against the URA
# documentation you cite, and edit this tuple to match that version.
DEFAULT_SCHEDULES: tuple[PayeSchedule, ...] = (
    PayeSchedule(True, date(2012, 7, 1), None,
                 (Band(D(0), D(335000), D("0")), Band(D(335000), D(410000), D("0.20")),
                  Band(D(410000), D(485000), D("0.25")), Band(D(485000), None, D("0.30"))),
                 source="Assignment baseline (resident, monthly); verify against URA"),
    PayeSchedule(False, date(2012, 7, 1), None,
                 (Band(D(0), D(410000), D("0.25")), Band(D(410000), None, D("0.30"))),
                 source="Non-resident, monthly; verify against URA"),
)


class PayeTax(Tax):
    """Pay As You Earn using the schedule effective on the context date."""
    name = "PAYE"

    def __init__(self, schedules: tuple[PayeSchedule, ...] = DEFAULT_SCHEDULES) -> None:
        self.schedules = schedules

    def find_schedule(self, as_of: date, resident: bool) -> PayeSchedule:
        for s in self.schedules:
            if s.applies(as_of, resident):
                return s
        raise NoScheduleForDateError(f"No {'resident' if resident else 'non-resident'} PAYE schedule for {as_of}")

    def compute_on(self, chargeable: Money, ctx: PayrollContext) -> TaxResult:
        """PAYE on an explicit chargeable income (used by the pipeline after deductions)."""
        if chargeable < Money.zero():
            raise InvalidIncomeError(f"Chargeable income cannot be negative: {chargeable}")
        schedule = self.find_schedule(ctx.as_of, ctx.resident)
        return TaxResult(self.name, chargeable, schedule.compute(chargeable), notes=schedule.source)

    def calculate(self, ctx: PayrollContext) -> TaxResult:
        self._require(ctx, PayrollContext)
        return self.compute_on(ctx.total_emoluments, ctx)


class NssfTax(Tax):
    """NSSF: employee 5%, employer 10% of gross emoluments."""
    name = "NSSF"

    def __init__(self, employee_rate: Decimal = Decimal("0.05"), employer_rate: Decimal = Decimal("0.10")) -> None:
        self.employee_rate, self.employer_rate = employee_rate, employer_rate

    def calculate(self, ctx: PayrollContext) -> TaxResult:
        self._require(ctx, PayrollContext)
        base = ctx.total_emoluments
        employee, employer = base * self.employee_rate, base * self.employer_rate
        return TaxResult(self.name, base, employee, {"employee": employee, "employer": employer})


class LocalServiceTax(Tax):
    """Configurable monthly Local Service Tax, capped at the month's emoluments."""
    name = "Local Service Tax"

    def __init__(self, monthly_amount: Money = Money.zero()) -> None:
        if not isinstance(monthly_amount, Money) or monthly_amount < Money.zero():
            raise InvalidIncomeError("monthly_amount must be a non-negative Money")
        self.monthly_amount = monthly_amount

    def calculate(self, ctx: PayrollContext) -> TaxResult:
        self._require(ctx, PayrollContext)
        return TaxResult(self.name, ctx.total_emoluments, min(self.monthly_amount, ctx.total_emoluments))


@dataclass(frozen=True)
class Payslip:
    """Result of the payroll pipeline."""
    total_emoluments: Money
    nssf_employee: Money
    nssf_employer: Money
    local_service_tax: Money
    chargeable_income: Money
    paye: Money
    net_pay: Money
    employer_cost: Money

    def lines(self) -> list[tuple[str, Money]]:
        return [("Total emoluments (gross + allowances)", self.total_emoluments),
                ("NSSF employee (5%)", self.nssf_employee), ("Local Service Tax", self.local_service_tax),
                ("Chargeable income for PAYE", self.chargeable_income), ("PAYE", self.paye),
                ("NET PAY", self.net_pay), ("NSSF employer (10%), employer cost", self.nssf_employer),
                ("Total employer cost", self.employer_cost)]


class PayrollPipeline:
    """Composes NSSF, LST and PAYE. Order of deductions:

    1. total emoluments = gross + allowances
    2. NSSF on total emoluments (employee 5%, employer 10%)
    3. Local Service Tax is deducted BEFORE PAYE (assignment rule), so it lowers chargeable income
    4. NSSF reduces PAYE base only if ``nssf_deductible_for_paye`` is True. Default False: confirm
       with URA; the employee contribution is generally not deductible for PAYE
    5. PAYE on chargeable income
    6. net pay = emoluments - NSSF(employee) - LST - PAYE (employer NSSF is an employer cost)
    """

    def __init__(self, nssf: Optional[NssfTax] = None, lst: Optional[LocalServiceTax] = None,
                 paye: Optional[PayeTax] = None, nssf_deductible_for_paye: bool = False) -> None:
        self.nssf = nssf or NssfTax()
        self.lst = lst or LocalServiceTax()
        self.paye = paye or PayeTax()
        self.nssf_deductible_for_paye = nssf_deductible_for_paye

    def run(self, ctx: PayrollContext) -> Payslip:
        emol = ctx.total_emoluments
        nssf = self.nssf.calculate(ctx)
        lst = self.lst.calculate(ctx)
        chargeable = emol - lst.amount
        if self.nssf_deductible_for_paye:
            chargeable = chargeable - nssf.amount
        chargeable = max(chargeable, Money.zero())
        paye = self.paye.compute_on(chargeable, ctx)
        net = emol - nssf.amount - lst.amount - paye.amount
        return Payslip(emol, nssf.amount, nssf.breakdown["employer"], lst.amount, chargeable,
                       paye.amount, net, emol + nssf.breakdown["employer"])

##
%%writefile src/vat_cit.py
"""VAT and corporate income tax modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .base import CorporateContext, Tax, TaxContext, TaxResult
from .errors import InvalidInvoiceError
from .money import Money


class VatCategory(Enum):
    STANDARD = "standard"
    ZERO_RATED = "zero-rated"
    EXEMPT = "exempt"


class Direction(Enum):
    SALE = "sale"          # output VAT
    PURCHASE = "purchase"  # input VAT


@dataclass(frozen=True)
class Invoice:
    """VAT-exclusive invoice line."""
    invoice_id: str
    net_amount: Money
    category: VatCategory
    direction: Direction

    def __post_init__(self) -> None:
        if not isinstance(self.net_amount, Money) or self.net_amount < Money.zero():
            raise InvalidInvoiceError(f"Invoice {self.invoice_id!r}: net_amount must be non-negative Money")
        if not isinstance(self.category, VatCategory) or not isinstance(self.direction, Direction):
            raise InvalidInvoiceError(f"Invoice {self.invoice_id!r}: bad category/direction")


@dataclass(frozen=True)
class VatContext(TaxContext):
    """A list of invoices for one VAT period."""
    invoices: tuple[Invoice, ...] = field(default_factory=tuple)


class VatTax(Tax):
    """Output VAT - claimable input VAT.

    Simplifications (state them in your report): VAT is rounded per invoice; if the business
    also makes exempt sales, input VAT is claimable pro-rata (taxable sales / total sales).
    A negative result means a refund/credit position.
    """
    name = "VAT"

    def __init__(self, rate: Decimal = Decimal("0.18")) -> None:
        self.rate = rate

    def calculate(self, ctx: VatContext) -> TaxResult:
        self._require(ctx, VatContext)
        output = input_vat = taxable_sales = exempt_sales = Money.zero()
        for inv in ctx.invoices:
            if inv.direction is Direction.SALE:
                if inv.category is VatCategory.STANDARD:
                    output += inv.net_amount * self.rate
                    taxable_sales += inv.net_amount
                elif inv.category is VatCategory.ZERO_RATED:
                    taxable_sales += inv.net_amount
                else:
                    exempt_sales += inv.net_amount
            elif inv.category is VatCategory.STANDARD:
                input_vat += inv.net_amount * self.rate
        ratio = Decimal(1) if not exempt_sales else taxable_sales.amount / (taxable_sales + exempt_sales).amount
        claimable = input_vat * ratio
        return TaxResult(self.name, taxable_sales, output - claimable,
                         {"output_vat": output, "input_vat_incurred": input_vat, "input_vat_claimable": claimable},
                         notes="Negative amount = VAT refundable/carried forward")


class CorporateIncomeTax(Tax):
    """Chargeable income = revenue - allowable deductions (floored at zero), times the rate."""
    name = "Corporate Income Tax"

    def __init__(self, rate: Decimal = Decimal("0.30")) -> None:
        self.rate = rate

    def calculate(self, ctx: CorporateContext) -> TaxResult:
        self._require(ctx, CorporateContext)
        chargeable = max(ctx.revenue - ctx.deductions, Money.zero())
        note = "Loss: no tax (loss carry-forward not modelled)" if ctx.deductions > ctx.revenue else ""
        return TaxResult(self.name, chargeable, chargeable * self.rate, notes=note)

##
%%writefile src/planner.py
"""Extension B: compare salary vs dividend for extracting profit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from .base import CorporateContext, PayrollContext
from .money import Money
from .payroll import PayrollPipeline
from .vat_cit import CorporateIncomeTax


@dataclass(frozen=True)
class ScenarioOutcome:
    name: str
    total_tax: Money        # PAYE + CIT + dividend WHT
    contributions: Money    # NSSF employee + employer (savings, not a pure tax)
    net_to_owner: Money


class Scenario(Protocol):
    name: str
    def evaluate(self, pre_tax_profit: Money, as_of: date) -> ScenarioOutcome: ...


class SalaryScenario:
    """Spend the whole pre-tax profit on a monthly salary (incl. 10% employer NSSF) for 12 months."""
    name = "Salary"

    def __init__(self, payroll: PayrollPipeline, cit: CorporateIncomeTax, months: int = 12) -> None:
        self.payroll, self.cit, self.months = payroll, cit, months

    def evaluate(self, pre_tax_profit: Money, as_of: date) -> ScenarioOutcome:
        gross = Money(pre_tax_profit.amount / (self.months * (1 + self.payroll.nssf.employer_rate)))
        slip = self.payroll.run(PayrollContext(as_of=as_of, gross=gross))
        cost = slip.employer_cost * self.months
        cit = self.cit.calculate(CorporateContext(as_of=as_of, revenue=pre_tax_profit, deductions=min(cost, pre_tax_profit)))
        return ScenarioOutcome(self.name, slip.paye * self.months + cit.amount,
                               (slip.nssf_employee + slip.nssf_employer) * self.months, slip.net_pay * self.months)


class DividendScenario:
    """Pay CIT, then distribute the rest as a dividend subject to withholding tax."""
    name = "Dividend"

    def __init__(self, cit: CorporateIncomeTax, wht_rate: Decimal = Decimal("0.15")) -> None:
        self.cit, self.wht_rate = cit, wht_rate  # verify the current dividend WHT rate

    def evaluate(self, pre_tax_profit: Money, as_of: date) -> ScenarioOutcome:
        cit = self.cit.calculate(CorporateContext(as_of=as_of, revenue=pre_tax_profit)).amount
        dividend = pre_tax_profit - cit
        wht = dividend * self.wht_rate
        return ScenarioOutcome(self.name, cit + wht, Money.zero(), dividend - wht)


@dataclass(frozen=True)
class Comparison:
    a: ScenarioOutcome
    b: ScenarioOutcome

    @property
    def net_difference(self) -> Money:
        return self.a.net_to_owner - self.b.net_to_owner

    @property
    def better(self) -> str:
        return self.a.name if self.a.net_to_owner > self.b.net_to_owner else self.b.name


class TaxPlanner:
    """Compares two scenarios on the same pre-tax profit."""

    def compare(self, a: Scenario, b: Scenario, pre_tax_profit: Money, as_of: date) -> Comparison:
        return Comparison(a.evaluate(pre_tax_profit, as_of), b.evaluate(pre_tax_profit, as_of))

##
import importlib, pandas as pd
from datetime import date
from decimal import Decimal
from IPython.display import display
import src.money, src.base, src.payroll, src.vat_cit, src.planner
for m in (src.money, src.base, src.payroll, src.vat_cit, src.planner): importlib.reload(m)
from src.money import Money
from src.base import PayrollContext, CorporateContext
from src.errors import *
from src.payroll import PayeTax, PayrollPipeline, LocalServiceTax
from src.vat_cit import VatTax, VatContext, Invoice, VatCategory, Direction, CorporateIncomeTax
from src.planner import TaxPlanner, SalaryScenario, DividendScenario

TODAY = date(2026, 10, 11)

# Payroll
pipeline = PayrollPipeline(lst=LocalServiceTax(Money(5000)))
slip = pipeline.run(PayrollContext(as_of=TODAY, gross=Money(2_000_000), allowances=Money(300_000)))
display(pd.DataFrame([(k, str(v)) for k, v in slip.lines()], columns=["Item", "Amount"]))

# VAT
invoices = (Invoice("S1", Money(10_000_000), VatCategory.STANDARD, Direction.SALE),
            Invoice("S2", Money(4_000_000), VatCategory.ZERO_RATED, Direction.SALE),
            Invoice("P1", Money(6_000_000), VatCategory.STANDARD, Direction.PURCHASE))
vat = VatTax().calculate(VatContext(as_of=TODAY, invoices=invoices))
print(vat.amount, {k: str(v) for k, v in vat.breakdown.items()})

# Corporate tax
print(CorporateIncomeTax().calculate(CorporateContext(as_of=TODAY, revenue=Money(500_000_000), deductions=Money(320_000_000))))

# Errors are loud
for label, fn in [("negative income", lambda: PayrollContext(as_of=TODAY, gross=Money(-1))),
                  ("no schedule", lambda: PayeTax().calculate(PayrollContext(as_of=date(2000, 1, 1), gross=Money(1)))),
                  ("float", lambda: Money(100) * 0.18)]:
    try: fn()
    except (TaxError, TypeError) as e: print(f"{label}: {type(e).__name__}: {e}")


##
cit = CorporateIncomeTax()
cmp = TaxPlanner().compare(SalaryScenario(PayrollPipeline(), cit), DividendScenario(cit), Money(120_000_000), TODAY)
display(pd.DataFrame([{"Scenario": o.name, "Total tax": str(o.total_tax), "NSSF contributions": str(o.contributions),
                       "Net to owner": str(o.net_to_owner)} for o in (cmp.a, cmp.b)]))
print(f"Better for the owner: {cmp.better}; net difference {cmp.net_difference}")

##
%%writefile tests/test_tax.py
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from hypothesis import given, settings, strategies as st
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.base import CorporateContext, PayrollContext, TaxContext
from src.errors import InvalidIncomeError, InvalidContextError, NoScheduleForDateError
from src.money import Money
from src.payroll import NssfTax, PayeTax, PayrollPipeline
from src.vat_cit import CorporateIncomeTax, Direction, Invoice, VatCategory, VatContext, VatTax

D = date(2026, 10, 11)

def paye(income: int, resident: bool = True) -> Money:
    ctx = PayrollContext(as_of=D, gross=Money(income), resident=resident)
    return PayeTax().calculate(ctx).amount

@pytest.mark.parametrize("income,expected", [
    (0, 0), (335_000, 0), (335_001, 0), (335_010, 2), (410_000, 15_000), (485_000, 33_750),
    (500_000, 38_250), (1_000_000, 188_250), (10_000_000, 2_888_250), (10_000_001, 2_888_250)])
def test_resident_band_edges(income, expected):
    assert paye(income) == Money(expected)

def test_non_resident_schedule():
    assert paye(410_000, resident=False) == Money(102_500)

def test_surcharge_above_10m():
    # 40% marginal above 10,000,000: 30% band + 10% surcharge
    assert paye(11_000_000) - paye(10_000_000) == Money(400_000)

@settings(max_examples=200)
@given(st.integers(0, 10**10), st.booleans())
def test_non_negative_and_never_exceeds_income(income, resident):
    t = paye(income, resident)
    assert t >= Money(0) and t <= Money(income)

@settings(max_examples=200)
@given(st.integers(0, 10**10), st.integers(0, 10**10), st.booleans())
def test_monotonic(a, b, resident):
    lo, hi = sorted((a, b))
    assert paye(lo, resident) <= paye(hi, resident)

def test_money_rejects_float_and_rounds_half_up():
    with pytest.raises(TypeError):
        Money(0.1)
    with pytest.raises(TypeError):
        Money(100) * 0.18
    assert Money("2.5") == Money(3) and Money("2.4") == Money(2)
    assert sum([Money(1), Money(2)]) == Money(3)

def test_errors():
    with pytest.raises(InvalidIncomeError):
        PayrollContext(as_of=D, gross=Money(-1))
    with pytest.raises(NoScheduleForDateError):
        PayeTax().calculate(PayrollContext(as_of=date(2000, 1, 1), gross=Money(1)))
    with pytest.raises(InvalidContextError):
        PayeTax().calculate(TaxContext(as_of=D))

def test_nssf_and_payroll_identity():
    ctx = PayrollContext(as_of=D, gross=Money(2_000_000), allowances=Money(300_000))
    n = NssfTax().calculate(ctx)
    assert n.amount == Money(115_000) and n.breakdown["employer"] == Money(230_000)
    slip = PayrollPipeline().run(ctx)
    assert slip.paye == Money(578_250)
    assert slip.net_pay == slip.total_emoluments - slip.nssf_employee - slip.local_service_tax - slip.paye

def test_vat():
    inv = (Invoice("1", Money(1_000_000), VatCategory.STANDARD, Direction.SALE),
           Invoice("2", Money(500_000), VatCategory.ZERO_RATED, Direction.SALE),
           Invoice("3", Money(400_000), VatCategory.STANDARD, Direction.PURCHASE))
    r = VatTax().calculate(VatContext(as_of=D, invoices=inv))
    assert r.breakdown["output_vat"] == Money(180_000) and r.amount == Money(108_000)

def test_cit():
    r = CorporateIncomeTax().calculate(CorporateContext(as_of=D, revenue=Money(1_000_000), deductions=Money(400_000)))
    assert r.amount == Money(180_000)
    loss = CorporateIncomeTax().calculate(CorporateContext(as_of=D, revenue=Money(100), deductions=Money(400)))
    assert loss.amount == Money(0)

##
from decimal import Decimal
print(0.1 + 0.2 == 0.3, 0.1 + 0.2)                               # False 0.30000000000000004
print(sum([0.1] * 10) == 1.0, sum([Decimal("0.1")] * 10) == 1)    # False True
print(round(2.675, 2), Decimal("2.675").quantize(Decimal("0.01")))  # 2.67 vs 2.68 (half-up)
# Realistic: 1 million invoices of UGX 0.1-type fractions drift in float, and a 0.5 UGX rounding can flip
print(sum(0.1 for _ in range(1_000_000)), sum(Decimal("0.1") for _ in range(1_000_000)))

###
#1. Why is float unsafe for tax computation? 
"""Binary float cannot represent most decimal fractions and thus errors accumulate and rounding decisions can flip i.e 7.675 to 3.67. A tax authority might need exact, reproducible, auditable cents and totals must reconcile."""

#2. Where would inheritance be justified here, and where did you deliberately avoid it?
#Justification
"""Tax is an abstract base with a polyphormic calculate. Custom exceptions share TaxError. PyrollContext, VatContext and CorporateContext are true specialization of TaxContent in that the base fixes the signature required."""
#Avoidance
"""The planner uses a protocol (duck typing) instead of a scenario hierarchy. Money has no Decimal subclass since we want a closed and safe API. PayrollPipeline is composed of NSSFTax, LocalServiceTax and PayeTax. There is no ResidentPaye or NonResidentPaye subclass."""