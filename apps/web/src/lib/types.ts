import type { EventType } from '@ticket/shared';

/** Response shapes the Phase 2 screens read. Kept narrow on purpose — these
 *  mirror the API's explicit `select`s, not the database rows. */

export type VenueSummary = { id: string; name: string; address: string; _count: { seats: number } };

export type VenueSeat = {
  id: string;
  section: string;
  row: string;
  number: number;
  posX: number;
  posY: number;
};

export type VenueDetail = { id: string; name: string; address: string; seats: VenueSeat[] };

export type Category = { id: string; name: string; price: string; sections?: string[] };

export type ShowSummary = { id: string; startsAt: string; _count?: { showSeats: number } };

export type EventSummary = {
  id: string;
  title: string;
  type: EventType;
  description: string | null;
  venue: { id: string; name: string; address: string };
  organiser: { id: string; name: string };
  categories: Category[];
  shows: ShowSummary[];
};

// Omit, not intersect: an intersection would leave `categories` as both
// Category[] and Required<Category>[], and TS keeps the looser one.
export type EventDetail = Omit<EventSummary, 'categories'> & { categories: Required<Category>[] };

export type OwnEvent = {
  id: string;
  title: string;
  type: EventType;
  venue: { id: string; name: string };
  categories: Required<Category>[];
  _count: { shows: number };
};
