import type { RequestHandler } from 'express';

/**
 * ponytail: six lines instead of morgan or pino. Upgrade to structured JSON
 * logging with request ids in Phase 9, when there is a log aggregator to read
 * it and concurrent seat requests need correlating.
 */
export const requestLogger: RequestHandler = (req, res, next) => {
  const start = performance.now();
  res.on('finish', () => {
    const ms = (performance.now() - start).toFixed(0);
    console.log(`${req.method} ${req.originalUrl} ${res.statusCode} ${ms}ms`);
  });
  next();
};
