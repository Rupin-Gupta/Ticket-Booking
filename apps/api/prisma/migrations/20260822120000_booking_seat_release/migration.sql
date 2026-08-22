-- Written by hand rather than generated, because the guarantee this migration
-- installs is one Prisma's schema language cannot express.
--
-- The original `showSeatId @unique` was described as a seatbelt: Postgres
-- refusing to record one show-seat in two bookings even if every application
-- check were wrong. It was too tight. A BookingSeat row survives cancellation
-- (revenue history and the cancellation email both need to know what was booked
-- and at what price), so the constraint kept occupying the seat forever and a
-- cancelled seat could never be sold again. Caught by a test asserting that a
-- released seat goes back on sale.
--
-- The real invariant was always "at most one LIVE claim per show-seat".

-- 1. Drop the over-tight constraint.
DROP INDEX IF EXISTS "BookingSeat_showSeatId_key";

-- 2. Cancellation marks a claim released rather than deleting the row.
ALTER TABLE "BookingSeat" ADD COLUMN "releasedAt" TIMESTAMP(3);

-- 3. A seat can appear in many bookings across time, but never twice in one.
CREATE UNIQUE INDEX "BookingSeat_bookingId_showSeatId_key"
  ON "BookingSeat"("bookingId", "showSeatId");

-- 4. Lookups by seat (verification, revenue) need this now the unique is gone.
CREATE INDEX "BookingSeat_showSeatId_idx" ON "BookingSeat"("showSeatId");

-- 5. The seatbelt, restored as a PARTIAL unique index. Rows with releasedAt set
--    are excluded, so a cancelled seat is sellable again while two live claims
--    on one seat remain impossible at the database level.
--
--    Prisma cannot represent this, so it is invisible to the schema file and a
--    future `migrate dev` may try to drop it as drift. If that happens, add it
--    back in the same migration — see docs/DEBUGGING.md.
CREATE UNIQUE INDEX "BookingSeat_showSeatId_live_key"
  ON "BookingSeat"("showSeatId")
  WHERE "releasedAt" IS NULL;
