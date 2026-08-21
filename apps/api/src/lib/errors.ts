/**
 * Every failure the API returns is an ApiError. One shape, one place that
 * decides the status code — routes throw, the error handler formats.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  static badRequest(code: string, message: string, details?: unknown) {
    return new ApiError(400, code, message, details);
  }
  static unauthorized(message = 'Authentication required.') {
    return new ApiError(401, 'UNAUTHORIZED', message);
  }
  static forbidden(message = 'You do not have access to this resource.') {
    return new ApiError(403, 'FORBIDDEN', message);
  }
  static notFound(code: string, message: string) {
    return new ApiError(404, code, message);
  }
  /** Lost a race: seat taken, already waitlisted, offer already used. */
  static conflict(code: string, message: string) {
    return new ApiError(409, code, message);
  }
  /** Time-limited thing that has run out — an expired waitlist offer. */
  static gone(code: string, message: string) {
    return new ApiError(410, code, message);
  }
}
