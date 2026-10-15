#Mini-Project 2: Uganda Tax Engine
#Imports
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
from functools import total_ordering
from typing import Optional, Mapping, Protocol

import pandas as pd

#Custom Exceptions
class TaxError(Exception):
    """Base class for all tax engine errors."""


class InvalidIncomeError(TaxError):
    """Income or deduction is invalid."""


class NoScheduleForDateError(TaxError):
    """No PAYE schedule found."""


class InvalidContextError(TaxError):
    """Wrong context supplied."""


class InvalidInvoiceError(TaxError):
    """Invoice data is invalid."""

#Money Class
@total_ordering
class Money:

    def __init__(self, amount=0):

        if isinstance(amount, Money):
            amount = amount.amount

        if isinstance(amount, float):
            raise TypeError(
                "Float values are not permitted."
            )

        try:
            value = Decimal(str(amount))
        except (ValueError, InvalidOperation):
            raise TypeError(
                f"Invalid money value: {amount}"
            )

        self.amount = value.quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )

    @classmethod
    def zero(cls):
        return cls(0)

    def __add__(self, other):

        if not isinstance(other, Money):
            return NotImplemented

        return Money(
            self.amount + other.amount
        )

    def __sub__(self, other):

        if not isinstance(other, Money):
            return NotImplemented

        return Money(
            self.amount - other.amount
        )

    def __mul__(self, factor):

        if isinstance(factor, float):
            raise TypeError(
                "Use Decimal rather than float."
            )

        return Money(
            self.amount * Decimal(str(factor))
        )

    __rmul__ = __mul__

    def __lt__(self, other):

        if not isinstance(other, Money):
            return NotImplemented

        return self.amount < other.amount

    def __eq__(self, other):

        if not isinstance(other, Money):
            return False

        return self.amount == other.amount

    def __repr__(self):
        return f"Money('{self.amount}')"

    def __str__(self):
        return f"UGX {self.amount:,.0f}"

#Context Classes
@dataclass(frozen=True)
class TaxContext:
    as_of: date


@dataclass(frozen=True)
class PayrollContext(TaxContext):
    gross: Money
    allowances: Money = field(
        default_factory=Money.zero
    )
    resident: bool = True

    @property
    def total_emoluments(self):
        return self.gross + self.allowances


@dataclass(frozen=True)
class CorporateContext(TaxContext):
    revenue: Money
    deductions: Money


@dataclass(frozen=True)
class TaxResult:
    tax_name: str
    taxable_base: Money
    amount: Money
    breakdown: Mapping[str, Money] = field(
        default_factory=dict
    )
    notes: str = ""

#Abstract Tax Class
class Tax(ABC):

    @abstractmethod
    def calculate(
        self,
        ctx: TaxContext
    ) -> TaxResult:
        pass

#PAYE Structures
@dataclass(frozen=True)
class Band:
    lower: Decimal
    upper: Optional[Decimal]
    rate: Decimal

@dataclass(frozen=True)
class PayeSchedule:
    resident: bool
    bands: tuple


RESIDENT_BANDS = (
    Band(
        Decimal("0"),
        Decimal("335000"),
        Decimal("0")
    ),
    Band(
        Decimal("335000"),
        Decimal("410000"),
        Decimal("0.20")
    ),
    Band(
        Decimal("410000"),
        Decimal("485000"),
        Decimal("0.25")
    ),
    Band(
        Decimal("485000"),
        None,
        Decimal("0.30")
    )
)

NON_RESIDENT_BANDS = (
    Band(
        Decimal("0"),
        Decimal("410000"),
        Decimal("0.25")
    ),
    Band(
        Decimal("410000"),
        None,
        Decimal("0.30")
    )
)

#PAYE Calculator
class PayeTax(Tax):

    name = "PAYE"

    def calculate(
        self,
        ctx: PayrollContext
    ):

        income = ctx.total_emoluments.amount

        if ctx.resident:

            if income <= 335000:
                tax = Decimal("0")

            elif income <= 410000:
                tax = (
                    income - Decimal("335000")
                ) * Decimal("0.20")

            elif income <= 485000:
                tax = (
                    Decimal("15000")
                    +
                    (
                        income
                        - Decimal("410000")
                    ) * Decimal("0.25")
                )

            elif income <= 10000000:
                tax = (
                    Decimal("33750")
                    +
                    (
                        income
                        - Decimal("485000")
                    ) * Decimal("0.30")
                )

            else:
                tax = (
                    Decimal("33750")
                    +
                    (
                        Decimal("10000000")
                        - Decimal("485000")
                    )
                    * Decimal("0.30")
                    +
                    (
                        income
                        - Decimal("10000000")
                    )
                    * Decimal("0.10")
                )

        else:

            if income <= 410000:

                tax = (
                    income
                    * Decimal("0.25")
                )

            else:

                tax = (
                    Decimal("102500")
                    +
                    (
                        income
                        - Decimal("410000")
                    )
                    * Decimal("0.30")
                )

        return TaxResult(
            self.name,
            Money(income),
            Money(tax)
        )

#NSSF
class NssfTax(Tax):

    name = "NSSF"

    def calculate(
        self,
        ctx: PayrollContext
    ):

        base = ctx.total_emoluments

        employee = (
            base
            * Decimal("0.05")
        )

        employer = (
            base
            * Decimal("0.10")
        )

        return TaxResult(
            self.name,
            base,
            employee,
            {
                "employee": employee,
                "employer": employer
            }
        )

#Local Service Tax
class LocalServiceTax(Tax):

    name = "LST"

    def __init__(
        self,
        amount: Money
    ):
        self.amount = amount

    def calculate(
        self,
        ctx: PayrollContext
    ):

        return TaxResult(
            self.name,
            ctx.total_emoluments,
            self.amount
        )

#Payroll Pipeline
class PayrollPipeline:

    def __init__(
        self,
        lst_amount=Money(0)
    ):

        self.paye = PayeTax()
        self.nssf = NssfTax()
        self.lst = LocalServiceTax(
            lst_amount
        )

    def run(self, ctx):

        total = ctx.total_emoluments

        nssf_result = self.nssf.calculate(ctx)

        lst_result = self.lst.calculate(ctx)

        chargeable = (
            total
            -
            nssf_result.amount
            -
            lst_result.amount
        )

        paye_result = self.paye.calculate(
            PayrollContext(
                as_of=ctx.as_of,
                gross=chargeable,
                allowances=Money.zero(),
                resident=ctx.resident
            )
        )

        net_pay = (
            total
            -
            nssf_result.amount
            -
            lst_result.amount
            -
            paye_result.amount
        )

        return {
            "Total Emoluments": total,
            "NSSF Employee": nssf_result.amount,
            "NSSF Employer":
                nssf_result.breakdown["employer"],
            "Local Service Tax":
                lst_result.amount,
            "PAYE":
                paye_result.amount,
            "Net Pay":
                net_pay
        }
#VAT Enumerations
class VatCategory(Enum):
    STANDARD = "standard"
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"


class Direction(Enum):
    SALE = "sale"
    PURCHASE = "purchase"

#Invoice Class
@dataclass(frozen=True)
class Invoice:

    invoice_id: str
    net_amount: Money
    category: VatCategory
    direction: Direction

    def __post_init__(self):

        if self.net_amount < Money.zero():

            raise InvalidInvoiceError(
                "Invoice amounts cannot be negative."
            )

#VAT Context
@dataclass(frozen=True)
class VatContext(TaxContext):

    invoices: tuple[Invoice, ...] = field(
        default_factory=tuple
    )

#VAT Tax Class
class VatTax(Tax):

    RATE = Decimal("0.18")

    def calculate(
        self,
        ctx: VatContext
    ) -> TaxResult:

        output_vat = Money.zero()
        input_vat = Money.zero()

        taxable_sales = Money.zero()
        exempt_sales = Money.zero()

        for invoice in ctx.invoices:

            # SALES

            if invoice.direction == Direction.SALE:

                if invoice.category == VatCategory.STANDARD:

                    vat = (
                        invoice.net_amount
                        * self.RATE
                    )


                    output_vat += vat
                    taxable_sales += invoice.net_amount

                elif invoice.category == VatCategory.ZERO_RATED:

                    taxable_sales += invoice.net_amount

                elif invoice.category == VatCategory.EXEMPT:

                    exempt_sales += invoice.net_amount

            # PURCHASES

            elif invoice.direction == Direction.PURCHASE:

                if invoice.category == VatCategory.STANDARD:

                    vat = (
                        invoice.net_amount
                        * self.RATE
                    )

                    input_vat += vat

        net_vat = output_vat - input_vat

        return TaxResult(
            tax_name="VAT",
            taxable_base=taxable_sales,
            amount=net_vat,
            breakdown={
                "Output VAT": output_vat,
                "Input VAT": input_vat,
                "Taxable Sales": taxable_sales,
                "Exempt Sales": exempt_sales
            },
            notes=(
                "Positive = payable to URA. "
                "Negative = refundable/credit."
            ))

#Demonstrate VAT Calculation
TODAY = date(2026, 10, 11)

vat_invoices = (

    Invoice(
        "S1",
        Money(10_000_000),
        VatCategory.STANDARD,
        Direction.SALE
    ),

    Invoice(
        "S2",
        Money(4_000_000),
        VatCategory.ZERO_RATED,
        Direction.SALE
    ),

    Invoice(
        "S3",
        Money(2_000_000),
        VatCategory.EXEMPT,
        Direction.SALE
    ),

    Invoice(
        "P1",
        Money(6_000_000),
        VatCategory.STANDARD,
        Direction.PURCHASE
    )
)

vat_result = VatTax().calculate(

    VatContext(
        as_of=TODAY,
        invoices=vat_invoices
    )
)

print("VAT RESULT")
print("=" * 40)

print(
    "Net VAT:",
    vat_result.amount
)

print()

for k, v in vat_result.breakdown.items():

    print(f"{k}: {v}")

#Corporate Income Tax (CIT)
class CorporateIncomeTax(Tax):

    RATE = Decimal("0.30")

    def calculate(
        self,
        ctx: CorporateContext
    ) -> TaxResult:

        chargeable_income = max(
            ctx.revenue - ctx.deductions,
            Money.zero()
        )

        tax = (
            chargeable_income
            * self.RATE
        )

        return TaxResult(
            tax_name="Corporate Income Tax",
            taxable_base=chargeable_income,
            amount=tax,
            breakdown={
                "Revenue": ctx.revenue,
                "Deductions": ctx.deductions,
                "Chargeable Income":
                    chargeable_income
            }
        )

#Demonstrate Corporate Income Tax
cit = CorporateIncomeTax()

cit_result = cit.calculate(

    CorporateContext(
        as_of=TODAY,
        revenue=Money(500_000_000),
        deductions=Money(320_000_000)
    )
)

print(
    "\nCORPORATE INCOME TAX"
)

print("=" * 40)

print(
    "Chargeable Income:",
    cit_result.taxable_base
)

print(
    "Tax Payable:",
    cit_result.amount
)

#Corporate Tax Loss Example
loss_result = cit.calculate(

    CorporateContext(
        as_of=TODAY,
        revenue=Money(50_000_000),
        deductions=Money(100_000_000)
    )
)

print(
    "\nLOSS POSITION"
)

print("=" * 40)

print(
    "Chargeable Income:",
    loss_result.taxable_base
)

print(
    "Corporate Tax:",
    loss_result.amount
)

#CIT Results Table
pd.DataFrame(
    [
        {
            "Description": "Revenue",
            "Amount": str(
                cit_result.breakdown["Revenue"]
            )
        },
        {
            "Description": "Deductions",
            "Amount": str(
                cit_result.breakdown["Deductions"]
            )
        },
        {
            "Description": "Chargeable Income",
            "Amount": str(
                cit_result.breakdown["Chargeable Income"]
            )
        },
        {
            "Description": "Tax Payable",
            "Amount": str(
                cit_result.amount
            )
        }
    ]
)

#1. Why is float unsafe for tax computation? 
"""Binary float cannot represent most decimal fractions and thus errors accumulate and rounding decisions can flip i.e 7.675 to 3.67. A tax authority might need exact, reproducible, auditable cents and totals must reconcile."""

#2. Where would inheritance be justified here, and where did you deliberately avoid it?
#Justification
"""Tax is an abstract base with a polyphormic calculate. Custom exceptions share TaxError. PyrollContext, VatContext and CorporateContext are true specialization of TaxContent in that the base fixes the signature required."""
#Avoidance
"""The planner uses a protocol (duck typing) instead of a scenario hierarchy. Money has no Decimal subclass since we want a closed and safe API. PayrollPipeline is composed of NSSFTax, LocalServiceTax and PayeTax. There is no ResidentPaye or NonResidentPaye subclass."""
