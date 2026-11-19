import { useState } from 'react'
import { t } from '../../i18n'
import { Segmented } from '../../ui'
import type { ImportOptions } from './types'

export const STREAM_TOP_MIN = 1
export const STREAM_TOP_MAX = 20
export const STREAM_TOP_DEFAULT = 8
const LAYOUTS = ['auto', 'stack', 'crop'] as const

/** How many highlights to clip: a whole number in 1..20, the default when the field is empty or not a number. */
export function clampStreamTop(raw: string | number | null | undefined): number {
  const n = Math.floor(Number(raw))
  if (raw === '' || raw == null || !Number.isFinite(n)) return STREAM_TOP_DEFAULT
  return Math.min(STREAM_TOP_MAX, Math.max(STREAM_TOP_MIN, n))
}

/** Extra import form fields for stream mode. Empty when the mode is off, so a normal import is byte-for-byte unchanged. */
export function streamFormFields(options: Partial<ImportOptions>): [string, string][] {
  if (!options.stream) return []
  const layout = LAYOUTS.includes(options.stream_layout as typeof LAYOUTS[number]) ? options.stream_layout as string : 'auto'
  return [['stream', 'true'], ['stream_top', String(clampStreamTop(options.stream_top))], ['stream_layout', layout]]
}

/** A stream is clipped by chat and sound; transcription only adds captions, so a missing Whisper must not block the import. */
export function transcriptNotRequired(options: Partial<ImportOptions>, hasSubtitle: boolean): boolean {
  return hasSubtitle || !!options.stream
}

interface Props {
  value: ImportOptions
  onChange: (next: ImportOptions) => void
  /** Called with the chosen chat file (or null). The page keeps it in a ref, so this panel owns the display state. */
  onChat: (file: File | null) => void
  showChat: boolean
  disabled?: boolean
}

export default function StreamModePanel({ value, onChange, onChat, showChat, disabled }: Props) {
  const on = !!value.stream
  const [chat, setChat] = useState<File | null>(null)
  const pick = (file: File | null) => { setChat(file); onChat(file) }
  return (
    <div className="studio-field studio-stream-mode" data-testid="stream-mode">
      <label className="studio-check">
        <input type="checkbox" checked={on} disabled={disabled} aria-label={t('直播回放模式')}
          onChange={e => onChange({ ...value, stream: e.target.checked, stream_top: value.stream_top ?? STREAM_TOP_DEFAULT, stream_layout: value.stream_layout ?? 'auto', ...(e.target.checked ? { source_window: null } : {}) })} />
        <span>{t('直播回放模式')}</span>
      </label>
      <small className="studio-muted">{t('按聊天热度和音量找高光，只处理有看点的片段，适合几小时的直播回放。')}</small>
      {on && <div className="studio-stream-options">
        <label className="studio-field"><span>{t('剪几个高光')}</span>
          <input type="number" min={STREAM_TOP_MIN} max={STREAM_TOP_MAX} step={1} disabled={disabled} aria-label={t('剪几个高光')}
            value={value.stream_top ?? STREAM_TOP_DEFAULT} onChange={e => onChange({ ...value, stream_top: clampStreamTop(e.target.value) })} />
        </label>
        <div className="studio-field"><span>{t('构图')}</span>
          <Segmented size="sm" ariaLabel={t('构图')} value={value.stream_layout ?? 'auto'}
            options={[{ value: 'auto', label: t('自动检测摄像头') }, { value: 'stack', label: t('上下分屏') }, { value: 'crop', label: t('满屏') }]}
            onChange={next => onChange({ ...value, stream_layout: next as ImportOptions['stream_layout'] })} />
        </div>
        {showChat
          ? <label className="studio-field">{t('聊天记录（选填）')}
              <input type="file" accept=".jsonl,.json,.txt" disabled={disabled} aria-label={t('聊天记录（选填）')} onChange={e => pick(e.target.files?.[0] || null)} />
              {chat && <small>{chat.name} <button type="button" className="studio-link" onClick={() => pick(null)}>{t('移除')}</button></small>}
              <small className="studio-muted">{t('链接导入会自动下载聊天；本地文件可上传聊天记录（JSONL 或 yt-dlp live_chat 文件）。')}</small>
            </label>
          : <small className="studio-muted">{t('链接导入会自动下载聊天；本地文件可上传聊天记录（JSONL 或 yt-dlp live_chat 文件）。')}</small>}
      </div>}
    </div>
  )
}
