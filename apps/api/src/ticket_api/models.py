"""
SQLAlchemy models mapped onto the EXISTING Prisma-generated schema.

The schema was deliberately not renamed during the port. Tables stay quoted
PascalCase, columns stay camelCase, enums stay native Postgres types. Renaming
would have been a second variable in a rewrite whose whole value is provable
equivalence — and the two hand-written DDL objects (the live-BookingSeat
partial unique index, and the venue-overlap exclusion constraint) are already
written against these names.

The cost is one explicit column name per attribute. Mechanical, and paid once.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    """
    Prisma's `@default(uuid())` generates client-side — the columns are plain
    TEXT with no database default. So the application still owns id generation.
    """
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """
    Naive UTC, to match `TIMESTAMP(3)` — Prisma's mapping has no time zone.

    Centralised because mixing aware and naive datetimes raises on comparison,
    and the seat expiry paths compare timestamps constantly. `datetime.utcnow()`
    is deprecated; this is the replacement that stays naive on purpose.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    """
    Format a naive-UTC timestamp exactly as JavaScript's `toISOString()` does.

    The trailing `Z` is not decoration. `datetime.isoformat()` on a naive value
    emits `2026-08-23T10:00:00` with no zone, and `new Date(...)` in the browser
    reads that as *local* time — so a hold countdown would be wrong by the
    viewer's UTC offset, silently, and only for users outside UTC.

    Milliseconds are included for the same reason: the retired API emitted them,
    and the frontend's countdown maths already assumes that shape.
    """
    if value is None:
        return None
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


def money(value: Decimal) -> str:
    """
    Render a price the way Prisma's `Decimal.toString()` did: "450", not
    "450.000000000000000000000000000000".

    The column is Numeric(65, 30), so a plain `str()` carries thirty zeros into
    the JSON. `format(..., "f")` rather than `str(normalize())` because
    normalize() renders whole numbers in exponent form — `Decimal("450.00")`
    becomes `4.5E+2`, which the frontend would parse but no human would trust.

    Still a string, never a float. Money must not acquire binary rounding on the
    way to the browser.
    """
    return format(value.normalize(), "f")


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- enums
# native_enum + create_type=False: the types already exist in the database.
# Letting SQLAlchemy emit CREATE TYPE would fail on every connection.


class Role(enum.StrEnum):
    CUSTOMER = "CUSTOMER"
    ORGANISER = "ORGANISER"
    ADMIN = "ADMIN"


class EventType(enum.StrEnum):
    MOVIE = "MOVIE"
    CONCERT = "CONCERT"


class StageLayout(enum.StrEnum):
    END_STAGE = "END_STAGE"  # audience faces one way, like a cinema
    CENTRE_STAGE = "CENTRE_STAGE"  # in the round, audience surrounds the stage


class ShowStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"


class SeatStatus(enum.StrEnum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    OFFERED = "OFFERED"  # held open for one specific waitlisted customer
    BOOKED = "BOOKED"


class BookingStatus(enum.StrEnum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class WaitlistStatus(enum.StrEnum):
    WAITING = "WAITING"
    OFFERED = "OFFERED"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"
    CANCELLED = "CANCELLED"


def pg_enum(py_enum: type[enum.Enum], name: str) -> ENUM:
    return ENUM(
        py_enum,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


def ts() -> TIMESTAMP:
    """TIMESTAMP(3) without time zone — exactly what Prisma created."""
    return TIMESTAMP(precision=3, timezone=False)


# -------------------------------------------------------------------- tables


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column("passwordHash", Text)
    role: Mapped[Role] = mapped_column(pg_enum(Role, "Role"), default=Role.CUSTOMER)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column("createdAt", ts(), default=utcnow)

    events_organised: Mapped[list[Event]] = relationship(back_populates="organiser")
    bookings: Mapped[list[Booking]] = relationship(back_populates="customer")
    waitlist_entries: Mapped[list[WaitlistEntry]] = relationship(back_populates="customer")


class Venue(Base):
    __tablename__ = "Venue"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)

    #: Admin-owned capabilities. An organiser books a venue; it does not book them.
    stage_layout: Mapped[StageLayout] = mapped_column(
        "stageLayout", pg_enum(StageLayout, "StageLayout"), default=StageLayout.END_STAGE
    )
    #: Which event types may be scheduled here. A CENTRE_STAGE venue may not
    #: allow MOVIE — nobody projects a film in the round.
    allowed_event_types: Mapped[list[EventType]] = mapped_column(
        "allowedEventTypes",
        ARRAY(pg_enum(EventType, "EventType")),
        default=lambda: [EventType.MOVIE, EventType.CONCERT],
    )
    #: Minutes the room stays unavailable after a show ends, for clearing and
    #: resetting. A stadium needs longer than a screening room.
    turnaround_minutes: Mapped[int] = mapped_column("turnaroundMinutes", Integer, default=15)

    seats: Mapped[list[Seat]] = relationship(back_populates="venue")
    events: Mapped[list[Event]] = relationship(back_populates="venue")


class Seat(Base):
    __tablename__ = "Seat"
    __table_args__ = (
        # One physical chair per label per venue.
        UniqueConstraint("venueId", "section", "row", "number", name="Seat_venue_label_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    venue_id: Mapped[str] = mapped_column("venueId", Text, ForeignKey("Venue.id"))
    section: Mapped[str] = mapped_column(Text)  # e.g. "Balcony", "Floor"
    row: Mapped[str] = mapped_column(Text)  # e.g. "A"
    number: Mapped[int] = mapped_column(Integer)  # e.g. 12
    pos_x: Mapped[float] = mapped_column("posX", Numeric(asdecimal=False))
    pos_y: Mapped[float] = mapped_column("posY", Numeric(asdecimal=False))

    venue: Mapped[Venue] = relationship(back_populates="seats")
    show_seats: Mapped[list[ShowSeat]] = relationship(back_populates="seat")


class Event(Base):
    __tablename__ = "Event"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    organiser_id: Mapped[str] = mapped_column("organiserId", Text, ForeignKey("User.id"))
    venue_id: Mapped[str] = mapped_column("venueId", Text, ForeignKey("Venue.id"))
    title: Mapped[str] = mapped_column(Text)
    type: Mapped[EventType] = mapped_column(pg_enum(EventType, "EventType"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    organiser: Mapped[User] = relationship(back_populates="events_organised")
    venue: Mapped[Venue] = relationship(back_populates="events")
    categories: Mapped[list[SeatCategory]] = relationship(back_populates="event")
    shows: Mapped[list[Show]] = relationship(back_populates="event")


class SeatCategory(Base):
    __tablename__ = "SeatCategory"
    __table_args__ = (UniqueConstraint("eventId", "name", name="SeatCategory_event_name_key"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column("eventId", Text, ForeignKey("Event.id"))
    name: Mapped[str] = mapped_column(Text)  # "Premium", "Standard"
    price: Mapped[Decimal] = mapped_column(Numeric(65, 30))
    #: Venue sections this price band covers — see ADR-016.
    sections: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    event: Mapped[Event] = relationship(back_populates="categories")
    show_seats: Mapped[list[ShowSeat]] = relationship(back_populates="category")
    waitlist_entries: Mapped[list[WaitlistEntry]] = relationship(back_populates="category")


class Show(Base):
    __tablename__ = "Show"
    __table_args__ = (Index("Show_venueId_startsAt_idx", "venueId", "startsAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column("eventId", Text, ForeignKey("Event.id"))

    #: Denormalised from event.venue so the venue-overlap exclusion constraint —
    #: which can only span one table — has something to key on. Safe because
    #: Event.venueId is immutable: moving an event would orphan every ShowSeat
    #: generated against the old venue's seats.
    venue_id: Mapped[str] = mapped_column("venueId", Text)

    starts_at: Mapped[datetime] = mapped_column("startsAt", ts())
    #: Supplied by the organiser; there is no sensible default for "how long is
    #: this show".
    duration_minutes: Mapped[int] = mapped_column("durationMinutes", Integer)
    ends_at: Mapped[datetime] = mapped_column("endsAt", ts())
    #: endsAt plus the venue's turnaround. This, not endsAt, is what blocks the
    #: room for another organiser.
    occupies_until: Mapped[datetime] = mapped_column("occupiesUntil", ts())
    status: Mapped[ShowStatus] = mapped_column(
        pg_enum(ShowStatus, "ShowStatus"), default=ShowStatus.SCHEDULED
    )

    event: Mapped[Event] = relationship(back_populates="shows")
    show_seats: Mapped[list[ShowSeat]] = relationship(back_populates="show")
    waitlist_entries: Mapped[list[WaitlistEntry]] = relationship(back_populates="show")
    bookings: Mapped[list[Booking]] = relationship(back_populates="show")


class ShowSeat(Base):
    __tablename__ = "ShowSeat"
    __table_args__ = (
        # Makes a double instantiation impossible rather than merely unlikely.
        UniqueConstraint("showId", "seatId", name="ShowSeat_show_seat_key"),
        # The seat map's only query shape: every seat in a show, by status.
        Index("ShowSeat_showId_status_idx", "showId", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    show_id: Mapped[str] = mapped_column("showId", Text, ForeignKey("Show.id"))
    seat_id: Mapped[str] = mapped_column("seatId", Text, ForeignKey("Seat.id"))
    category_id: Mapped[str] = mapped_column("categoryId", Text, ForeignKey("SeatCategory.id"))
    status: Mapped[SeatStatus] = mapped_column(
        pg_enum(SeatStatus, "SeatStatus"), default=SeatStatus.AVAILABLE
    )
    #: RULE 8 — never leaves the server. The public seat map must not reveal
    #: who is holding a seat.
    held_by_user_id: Mapped[str | None] = mapped_column("heldByUserId", Text, nullable=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column("holdExpiresAt", ts(), nullable=True)
    offer_expires_at: Mapped[datetime | None] = mapped_column("offerExpiresAt", ts(), nullable=True)

    show: Mapped[Show] = relationship(back_populates="show_seats")
    seat: Mapped[Seat] = relationship(back_populates="show_seats")
    category: Mapped[SeatCategory] = relationship(back_populates="show_seats")
    #: One per booking across time; at most one live — see ADR-020.
    booking_seats: Mapped[list[BookingSeat]] = relationship(back_populates="show_seat")


class Booking(Base):
    __tablename__ = "Booking"
    __table_args__ = (
        # Booking history: by customer, newest first.
        Index("Booking_customerId_createdAt_idx", "customerId", "createdAt"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    #: Human-facing, e.g. "BK-7F3K2".
    reference: Mapped[str] = mapped_column(Text, unique=True)
    customer_id: Mapped[str] = mapped_column("customerId", Text, ForeignKey("User.id"))
    show_id: Mapped[str] = mapped_column("showId", Text, ForeignKey("Show.id"))
    status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, "BookingStatus"), default=BookingStatus.CONFIRMED
    )
    #: Opaque bearer token encoded in the QR — RULE 10, 32 random bytes.
    qr_token: Mapped[str] = mapped_column("qrToken", Text, unique=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", ts(), default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column("cancelledAt", ts(), nullable=True)
    #: When the ticket was admitted at the door. A QR that verifies for ever is
    #: a QR that can be forwarded and used twice.
    checked_in_at: Mapped[datetime | None] = mapped_column("checkedInAt", ts(), nullable=True)

    customer: Mapped[User] = relationship(back_populates="bookings")
    show: Mapped[Show] = relationship(back_populates="bookings")
    seats: Mapped[list[BookingSeat]] = relationship(back_populates="booking")


class BookingSeat(Base):
    __tablename__ = "BookingSeat"
    __table_args__ = (
        UniqueConstraint("bookingId", "showSeatId", name="BookingSeat_booking_seat_key"),
        Index("BookingSeat_showSeatId_idx", "showSeatId"),
        # NOTE: the real seatbelt is a PARTIAL unique index that SQLAlchemy's
        # declarative layer cannot express here — see the baseline migration.
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column("bookingId", Text, ForeignKey("Booking.id"))
    #: NOT unique — see ADR-020. A plain unique made a cancelled seat
    #: unsellable forever. The seatbelt is a PARTIAL unique index on
    #: (showSeatId) WHERE releasedAt IS NULL, created by hand in migration
    #: 20260822120000_booking_seat_release.
    show_seat_id: Mapped[str] = mapped_column("showSeatId", Text, ForeignKey("ShowSeat.id"))
    price_at_booking: Mapped[Decimal] = mapped_column("priceAtBooking", Numeric(65, 30))
    #: Set on cancellation; the row survives for history.
    released_at: Mapped[datetime | None] = mapped_column("releasedAt", ts(), nullable=True)

    booking: Mapped[Booking] = relationship(back_populates="seats")
    show_seat: Mapped[ShowSeat] = relationship(back_populates="booking_seats")


class WaitlistEntry(Base):
    __tablename__ = "WaitlistEntry"
    __table_args__ = (
        # The FIFO queue pick: (show, category, WAITING) ordered by joinedAt.
        Index("WaitlistEntry_queue_idx", "showId", "categoryId", "status", "joinedAt"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    show_id: Mapped[str] = mapped_column("showId", Text, ForeignKey("Show.id"))
    category_id: Mapped[str] = mapped_column("categoryId", Text, ForeignKey("SeatCategory.id"))
    customer_id: Mapped[str] = mapped_column("customerId", Text, ForeignKey("User.id"))
    status: Mapped[WaitlistStatus] = mapped_column(
        pg_enum(WaitlistStatus, "WaitlistStatus"), default=WaitlistStatus.WAITING
    )
    joined_at: Mapped[datetime] = mapped_column("joinedAt", ts(), default=utcnow)
    offered_seat_id: Mapped[str | None] = mapped_column("offeredSeatId", Text, nullable=True)
    #: RULE 10 — bearer credential for a real seat, 32 random bytes.
    offer_token: Mapped[str | None] = mapped_column("offerToken", Text, unique=True, nullable=True)
    offer_expires_at: Mapped[datetime | None] = mapped_column("offerExpiresAt", ts(), nullable=True)

    show: Mapped[Show] = relationship(back_populates="waitlist_entries")
    category: Mapped[SeatCategory] = relationship(back_populates="waitlist_entries")
    customer: Mapped[User] = relationship(back_populates="waitlist_entries")


__all__ = [
    "Base",
    "Booking",
    "BookingSeat",
    "BookingStatus",
    "Event",
    "EventType",
    "Role",
    "Seat",
    "SeatCategory",
    "SeatStatus",
    "Show",
    "ShowSeat",
    "ShowStatus",
    "StageLayout",
    "User",
    "Venue",
    "WaitlistEntry",
    "WaitlistStatus",
    "new_id",
    "utcnow",
]
