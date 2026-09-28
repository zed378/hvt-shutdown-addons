// Minimal 5-field cron validation (minute hour day-of-month month day-of-week),
// matching what a Kubernetes CronJob accepts. Empty = "not scheduled".
const FIELD = /^(\*|\d+(-\d+)?)(\/\d+)?(,(\*|\d+(-\d+)?)(\/\d+)?)*$/;
const RANGES = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];
const MACROS = ['@yearly', '@annually', '@monthly', '@weekly', '@daily', '@midnight', '@hourly'];

export function cronError(expr) {
  const value = (expr || '').trim();

  if (!value || MACROS.includes(value)) {
    return '';
  }
  const parts = value.split(/\s+/);

  if (parts.length !== 5) {
    return 'Use 5 fields: minute hour day month weekday';
  }
  for (let i = 0; i < 5; i++) {
    if (!FIELD.test(parts[i])) {
      return `Invalid field "${ parts[i] }"`;
    }
    const [lo, hi] = RANGES[i];
    // Step values (after "/") are not range-checked.
    const stepless = parts[i].split(',').map((p) => p.split('/')[0]).join(',');
    const bounded = stepless.split(/[^\d]+/).filter(Boolean).map(Number);

    if (bounded.some((n) => n < lo || n > hi)) {
      return `"${ parts[i] }" must be within ${ lo }-${ hi }`;
    }
  }

  return '';
}

export const CRON_EXAMPLES = [
  { cron: '0 22 * * 5', text: 'Fri 22:00' },
  { cron: '0 6 * * 1', text: 'Mon 06:00' },
  { cron: '0 20 * * 1-5', text: 'weekdays 20:00' },
  { cron: '0 7 * * 1-5', text: 'weekdays 07:00' },
];
