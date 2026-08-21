import type { ErrorRequestHandler, RequestHandler } from 'express';
import { ZodError } from 'zod';
import { ApiError } from '../lib/errors.js';
import { isProd } from '../env.js';

export const notFound: RequestHandler = (req, _res, next) => {
  next(ApiError.notFound('ROUTE_NOT_FOUND', `No route for ${req.method} ${req.path}.`));
};

/**
 * The single place an error becomes a response body. Express 5 forwards
 * rejected promises here on its own, so route handlers need no try/catch
 * and no asyncHandler wrapper.
 */
export const errorHandler: ErrorRequestHandler = (err, _req, res, _next) => {
  if (err instanceof ApiError) {
    res.status(err.status).json({
      error: {
        code: err.code,
        message: err.message,
        ...(err.details ? { details: err.details } : {}),
      },
    });
    return;
  }

  if (err instanceof ZodError) {
    res.status(400).json({
      error: {
        code: 'VALIDATION_FAILED',
        message: 'Request validation failed.',
        details: err.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
      },
    });
    return;
  }

  // Anything reaching here is a bug, not a handled condition. Log it in full;
  // never leak the stack or the message to the client in production.
  console.error('[unhandled]', err);
  res.status(500).json({
    error: {
      code: 'INTERNAL_ERROR',
      message: isProd ? 'Something went wrong.' : err instanceof Error ? err.message : String(err),
    },
  });
};
