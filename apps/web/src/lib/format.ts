/**
 * Money arrives from the API as a decimal string and stays one. Number()
 * is only ever applied at the last moment, for display.
 */
const money = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

export const formatPrice = (value: string | number) => money.format(Number(value));

export const formatPriceRange = (prices: (string | number)[]) => {
  if (prices.length === 0) return null;
  const numbers = prices.map(Number).sort((a, b) => a - b);
  const low = numbers[0]!;
  const high = numbers[numbers.length - 1]!;
  return low === high ? formatPrice(low) : `${formatPrice(low)} – ${formatPrice(high)}`;
};

const dayMonth = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
});
const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });

export const formatShowDate = (iso: string) => dayMonth.format(new Date(iso));
export const formatShowTime = (iso: string) => time.format(new Date(iso));

/** For <time datetime="…"> — the machine-readable half of a date. */
export const isoDate = (iso: string) => new Date(iso).toISOString();
