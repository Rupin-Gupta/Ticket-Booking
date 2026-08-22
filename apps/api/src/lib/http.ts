import type { Request } from 'express';
import { ApiError } from './errors.js';

/**
 * Reads a route parameter as a string.
 *
 * Express types `req.params` values as `string | string[] | undefined` — a
 * repeated parameter really can arrive as an array, and under
 * `noUncheckedIndexedAccess` a missing one really can be undefined. Casting it
 * away would be a lie; this checks and fails with a 400 instead.
 */
export function param(req: Request, name: string): string {
  const value = req.params[name];
  if (typeof value !== 'string' || value.length === 0) {
    throw ApiError.badRequest('BAD_PARAM', `Missing or malformed :${name} in the URL.`);
  }
  return value;
}

/**
 * Drops keys whose value is `undefined`.
 *
 * `exactOptionalPropertyTypes` treats "absent" and "present but undefined" as
 * different things, and Prisma's update inputs mean "set this column to null"
 * by an explicit undefined in some positions. A PATCH body that omitted a field
 * must not be able to blank it, so strip the key rather than pass it through.
 */
export function compact<T extends object>(input: T): { [K in keyof T]: Exclude<T[K], undefined> } {
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined)) as {
    [K in keyof T]: Exclude<T[K], undefined>;
  };
}
