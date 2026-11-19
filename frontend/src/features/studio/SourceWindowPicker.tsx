import { useEffect, useRef, useState } from 'react'
import { t } from '../../i18n'
import { Btn } from '../../ui'
import type { SourceWindow } from './types'

/** Shortest window the pipeline still produces something from (matches the backend span check). */
export const MIN_WINDOW_SEC = 1

/**
 * Parse one time point typed by a person: `90`, `1:30`, `01:30`, `1:02:03`, `1.30` (min.sec? no — seconds only).
 * Returns null when the text is not a time, so the caller keeps the previous value instead of saving garbage.
 */
export function parseTimePoint(text: string): number | null {
  const raw = (text || '').trim().replace(/：/g, ':')
  if (!raw) return null
  const parts = raw.split(':')
  if (parts.length > 3) return null
  let total = 0
  for (const part of parts) {
    if (!/^\d+(\.\d+)?$/.test(part)) return null
    total = total * 60 + Number(part)
  }
  if (!Number.isFinite(total) || total < 0) return null
  return Math.round(total * 1000) / 1000
}

/** `m:ss`, or `h:mm:ss` past an hour. Whole seconds: the cut itself is frame-accurate. */
export function formatTimePoint(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return ''
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * Clamp a typed range onto the real source. `end` of null/unparsed keeps the previous one,
 * so a half-typed field never silently drops the whole window.
 */
export function normalizeWindow(
  start: number | null,
  end: number | null,
  durationSec: number | null | undefined,
): SourceWindow | null {
  if (start == null || end == null) return null
  const limit = durationSec != null && durationSec > 0 ? durationSec : null
  let from = Math.max(0, start)
  let to = Math.max(0, end)
  if (limit != null) {
    from = Math.min(from, limit)
    to = Math.min(to, limit)
  }
  if (to - from < MIN_WINDOW_SEC) return null
  return { start: Math.round(from * 1000) / 1000, end: Math.round(to * 1000) / 1000 }
}

/** The length of what will actually be processed: the window when set, otherwise the whole source. */
export function effectiveDuration(
  window: SourceWindow | null | undefined,
  durationSec: number | null | undefined,
): number | null {
  if (window && typeof window.end === 'number' && typeof window.start === 'number' && window.end > window.start) {
    return window.end - window.start
  }
  return durationSec ?? null
}

export function windowProblem(
  window: SourceWindow | null | undefined,
  durationSec: number | null | undefined,
  startText: string,
  endText: string,
): string | null {
  if (!startText.trim() && !endText.trim()) return null
  if (parseTimePoint(startText) == null || parseTimePoint(endText) == null) return t('时间格式不对，用 分:秒，例如 2:30')
  if (durationSec != null && durationSec > 0) {
    const end = parseTimePoint(endText) as number
    if (end > durationSec + .5) return t('结束时间超出视频长度')
  }
  if (!window) return t('结束时间要比开始时间晚')
  return null
}

/**
 * Optional「只处理这一段」control for a link import. Two time points on the original
 * timeline; the backend scans and cuts only inside them, so nothing outside is billed.
 */
export default function SourceWindowPicker({ window, durationSec, onChange }: {
  window?: SourceWindow | null
  durationSec?: number | null
  onChange: (window: SourceWindow | null) => void
}) {
  const [startText, setStartText] = useState(window ? formatTimePoint(window.start) : '')
  const [endText, setEndText] = useState(window ? formatTimePoint(window.end) : '')
  const emitted = useRef<SourceWindow | null>(window ?? null)
  // Adopt a window chosen elsewhere (the first-run 30-minute tip, 「处理全片」) without
  // overwriting what is being typed: only an external difference is copied in.
  useEffect(() => {
    const next = window ?? null
    if (next === emitted.current) return
    emitted.current = next
    setStartText(next ? formatTimePoint(next.start) : '')
    setEndText(next ? formatTimePoint(next.end) : '')
  }, [window])
  const problem = windowProblem(window, durationSec, startText, endText)
  const commit = (which: 'start' | 'end', text: string) => {
    const parsed = parseTimePoint(text)
    if (parsed == null) return
    const current = window ?? { start: 0, end: durationSec ?? 0 }
    const next = normalizeWindow(
      which === 'start' ? parsed : current.start,
      which === 'end' ? parsed : current.end,
      durationSec,
    )
    if (next === null) return
    emitted.current = next
    onChange(next)
  }
  const reset = () => {
    emitted.current = null
    onChange(null)
  }
  return <div className="studio-source-window" data-testid="source-window-picker">
    <div className="studio-source-window-row">
      <label className="studio-field">
        {t('从')}
        <input className="ac-input" aria-label={t('开始时间')} inputMode="numeric" placeholder="0:00"
          value={startText} onChange={e => setStartText(e.target.value)} onBlur={e => commit('start', e.target.value)} />
      </label>
      <label className="studio-field">
        {t('到')}
        <input className="ac-input" aria-label={t('结束时间')} inputMode="numeric"
          placeholder={formatTimePoint(durationSec ?? null) || '0:00'}
          value={endText} onChange={e => setEndText(e.target.value)} onBlur={e => commit('end', e.target.value)} />
      </label>
      {window && <Btn size="sm" variant="text" onClick={reset}>{t('处理全片')}</Btn>}
    </div>
    {problem && <p className="studio-error" role="alert">{problem}</p>}
    {!problem && window && (
      <p className="studio-muted">
        {t('只处理 {{from}} – {{to}}，共 {{minutes}} 分钟', {
          from: formatTimePoint(window.start),
          to: formatTimePoint(window.end),
          minutes: Math.max(1, Math.round((window.end - window.start) / 60)),
        })}
      </p>
    )}
  </div>
}