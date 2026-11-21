// Local-time helpers: the calendar date and the time with its UTC offset, from the clock's answer.
// Pure: no state, no clock of its own (the caller reads the clock and passes the value), and nothing
// here is ever logged.

const pad = (n, width = 2) => String(n).padStart(width, '0')

// A Date from the clock's answer (a Date or a number of milliseconds), null when it is not a usable time.
export function asDate(value) {
  const date = value instanceof Date ? value : typeof value === 'number' ? new Date(value) : null
  return date && Number.isFinite(date.getTime()) && date.getFullYear() >= 1 && date.getFullYear() <= 9999 ? date : null
}

/** The LOCAL calendar date of `value` as "YYYY-MM-DD"; null for an unusable time. */
export function localDate(value) {
  const date = asDate(value)
  return date && `${pad(date.getFullYear(), 4)}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** The local date and time with the UTC offset, "2026-11-17T20:15:03+09:00" (25 characters); null for an unusable time. */
export function localTime(value) {
  const date = asDate(value)
  if (!date) return null
  const offset = -Math.round(date.getTimezoneOffset())
  const sign = offset < 0 ? '-' : '+'
  const minutes = Math.abs(offset)
  return `${localDate(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`
}
