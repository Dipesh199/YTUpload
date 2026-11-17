import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import AutomationDashboard from './AutomationDashboard';
import {
  Check, ChevronDown, CircleAlert, Clapperboard, Download, FileVideo, FolderOpen, LayoutDashboard, Maximize2,
  FileJson, Gauge, Layers3, LoaderCircle, Music2, PackageCheck, Pause, Play, Plus, RefreshCw,
  PanelLeftClose, PanelLeftOpen, Save, Settings2, Sparkles, Square, Trash2, Type, Upload,
  Volume2, VolumeX, WandSparkles, X, ZoomIn,
} from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_URL || '';
const ACTIVE_JOB_STATUSES = ['queued', 'processing', 'cancelling'];
const ACTIVE_DOWNLOAD_STATUSES = ['queued', 'downloading', 'postprocessing', 'cancelling'];

const assetPreviewUrl = (path) => {
  const normalized = String(path || '').replaceAll('\\', '/').replace(/^assets\//, '').split('/').filter(Boolean).map(encodeURIComponent).join('/');
  return normalized ? `${API}/api/assets/${normalized}` : '';
};

const DEFAULT_TEMPLATE = {
  name: 'New preset',
  output_profile: { id: 'youtube-shorts', width: 1080, height: 1920, aspect_ratio: '9:16', preserve_source_fps: true },
  logo_overlay: { enabled: false, path: '', position: 'top-right', width: 180, opacity: 1, margin: 48, timing: { start: 0, end: null } },
  png_overlay: { enabled: false, path: '', position: 'center', width: 1080, opacity: 1, margin: 0, timing: { start: 0, end: null } },
  text: { enabled: false, value: 'Your text', position: 'bottom-center', font_size: 64, color: 'white', background: true, margin: 110, timing: { start: 0, end: null } },
  scale: { mode: 'cover', zoom: 1 },
  crop: { enabled: true, focal_x: 0.5, focal_y: 0.5 },
  mirror: { horizontal: false, vertical: false },
  image_adjustments: { enabled: false, contrast: 1, brightness: 0, saturation: 1, gamma: 1, sharpness: 0 },
  decorative_frame: { enabled: false, style: 'minimal', color: '#FFFFFF', thickness: 18, inset: 24, opacity: 0.9 },
  animated_elements: [],
  background_music: { enabled: false, path: '', volume: 0.2, fade_in: 1, fade_out: 2, timing: { start: 0, end: null } },
  beat_track: { enabled: false, asset: '', bpm: 100, time_signature: '4/4', offset: 0, volume: .12, loop: true, timing: { start: 0, end: null }, ducking: { enabled: true, speech_volume: .04, attack_ms: 120, release_ms: 450 } },
  beat_effects: [],
  motion_safety: { reduced_flashing: true },
  audio_mixer: { normalize: true, peak_limit: .95 },
  audio_tracks: [],
  original_audio_volume: 1,
  master_audio_volume: 1,
  intro: { enabled: false, path: '', volume: 1 },
  outro: { enabled: false, path: '', volume: 1 },
  watermark: { enabled: false, path: '', position: 'bottom-right', width: 140, opacity: 0.55, margin: 48, timing: { start: 0, end: null } },
};

const OUTPUT_PROFILES = [
  { id: 'youtube-1080p', name: 'YouTube Landscape', width: 1920, height: 1080, aspect_ratio: '16:9' },
  { id: 'youtube-1440p', name: 'YouTube 1440p', width: 2560, height: 1440, aspect_ratio: '16:9' },
  { id: 'youtube-shorts', name: 'YouTube Shorts', width: 1080, height: 1920, aspect_ratio: '9:16' },
  { id: 'preserve-source', name: 'Preserve Source', width: null, height: null, aspect_ratio: 'source' },
];

const EFFECT_TYPES = [
  ['twinkling_stars', 'Twinkling stars'], ['sparkle_burst', 'Sparkle burst'], ['cinematic_bars', 'Cinematic bars'],
  ['punch_zoom', 'Punch zoom'], ['camera_shake', 'Subtle camera shake'], ['glow_pulse', 'Glow pulse'],
  ['light_leak_sweep', 'Light-leak sweep'], ['animated_gradient', 'Animated gradient'], ['caption_bounce', 'Caption bounce'],
  ['shape_burst', 'Shape burst'], ['motion_lines', 'Motion lines'], ['radial_blur_pulse', 'Radial blur pulse'],
  ['chromatic_split', 'Chromatic split'], ['vignette_pulse', 'Vignette pulse'], ['film_grain_accent', 'Film-grain accent'],
  ['progress_indicator', 'Progress indicator'], ['section_title_reveal', 'Section-title reveal'],
];

const BACKGROUND_TRACKS = [
  { path: 'assets/cinematic-build.mp3', name: 'Cinematic Build', mood: 'Driving documentary', volume: 0.2 },
  { path: 'assets/bunker-pulse.mp3', name: 'Bunker Pulse', mood: 'Tense and energetic', volume: 0.16 },
  { path: 'assets/deep-focus.mp3', name: 'Deep Focus', mood: 'Calm and atmospheric', volume: 0.18 },
];

async function api(path, options) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* no JSON body */ }
    throw new Error(message);
  }
  return response.json();
}

const formatBytes = (bytes) => {
  if (!bytes) return '0 MB';
  return `${(bytes / 1024 / 1024).toFixed(bytes > 100 * 1024 * 1024 ? 0 : 1)} MB`;
};

const formatDuration = (seconds) => {
  const whole = Math.round(seconds || 0);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

const jobProgress = (job) => Math.max(0, Math.min(100, Math.round(Number(job?.progress) || 0)));

const wrapPreviewText = (value, fontSize) => {
  const maxCharacters = Math.max(12, Math.min(32, Math.floor(900 / Math.max(fontSize || 64, 16))));
  return String(value || '').split('\n').map((paragraph) => {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return '';
    return words.reduce((lines, word) => {
      const candidate = lines.length ? `${lines.at(-1)} ${word}` : word;
      if (lines.length && candidate.length <= maxCharacters) lines[lines.length - 1] = candidate;
      else lines.push(word);
      return lines;
    }, []).join('\n');
  }).join('\n');
};

const previewTextStyle = (config, backgroundOpacity, canvasWidth, canvasHeight) => {
  const fontSize = Math.max(16, Math.min(Number(config?.font_size) || 64, 240));
  const margin = Math.max(0, Number(config?.margin) || 0);
  return {
    fontSize: `${fontSize / Math.max(1, canvasWidth) * 100}cqw`,
    color: config?.color || 'white',
    '--preview-margin-x': `${margin / Math.max(1, canvasWidth) * 100}cqw`,
    '--preview-margin-y': `${margin / Math.max(1, canvasHeight) * 100}cqh`,
    '--preview-background': `rgba(0, 0, 0, ${backgroundOpacity})`,
  };
};

function PreviewTextLayer({ config, animated = false, canvasWidth = 1080, canvasHeight = 1920 }) {
  const lines = wrapPreviewText(config?.text ?? config?.value, config?.font_size).split('\n');
  return (
    <div className={`preview-text ${animated ? `preview-animated animation-${config?.animation || 'fade'}` : ''} ${config?.position || 'bottom-center'}`} style={previewTextStyle(config, animated ? .52 : .45, canvasWidth, canvasHeight)}>
      {lines.map((line, index) => <span className={`preview-text-line ${config?.background !== false ? 'has-background' : ''}`} key={`${line}-${index}`}>{line || '\u00a0'}</span>)}
    </div>
  );
}

function PreviewOverlayLayer({ config, canvasWidth = 1080, canvasHeight = 1920 }) {
  const source = assetPreviewUrl(config?.path);
  if (!source) return null;
  const margin = Math.max(0, Number(config?.margin) || 0);
  return (
    <img
      className={`preview-overlay ${config?.position || 'top-right'}`}
      src={source}
      alt=""
      draggable="false"
      style={{
        width: `${Math.max(24, Math.min(Number(config?.width) || 220, canvasWidth)) / Math.max(1, canvasWidth) * 100}cqw`,
        opacity: Math.max(0, Math.min(Number(config?.opacity ?? 1), 1)),
        '--preview-overlay-margin-x': `${margin / Math.max(1, canvasWidth) * 100}cqw`,
        '--preview-overlay-margin-y': `${margin / Math.max(1, canvasHeight) * 100}cqh`,
      }}
    />
  );
}

const beatEffectActiveAt = (effect, time, bpm) => {
  if (effect.enabled === false) return false;
  const start = Math.max(0, Number(effect.start) || 0);
  const end = effect.end == null || effect.end === '' ? Number.POSITIVE_INFINITY : Number(effect.end);
  if (time < start || time > end) return false;
  if (effect.trigger === 'manual') return true;
  let beats = Math.max(1, Number(effect.every_n_beats) || 1);
  if (effect.trigger === 'downbeat') beats *= 4;
  const period = Math.max(.08, (Number(effect.minimum_interval_ms) || 0) / 1000, 60 / Math.max(1, Number(bpm) || 100) * beats);
  const active = Math.max(.04, Math.min((Number(effect.duration_ms) || 160) / 1000, period * .8));
  const elapsed = Math.max(0, time - (Number(effect.beat_offset) || 0));
  return elapsed % period < active;
};

const seededPoints = (effect) => {
  let state = (Number(effect.seed) || 1001) >>> 0;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const count = Math.max(6, Math.min(28, Math.round(8 + (Number(effect.intensity) || .05) * 42)));
  return Array.from({ length: count }, (_, index) => {
    const burst = effect.type === 'sparkle_burst';
    return {
      id: index,
      left: burst ? 16 + random() * 68 : 4 + random() * 92,
      top: burst ? 20 + random() * 60 : 6 + random() * 88,
      size: .9 + random() * (1.2 + (Number(effect.intensity) || .05) * 5),
      delay: -random() * 1.8,
      speed: 1.1 + random() * 1.5,
    };
  });
};

function PreviewBeatEffects({ effects, currentTime, bpm }) {
  const visible = (effects || []).filter((effect) => beatEffectActiveAt(effect, currentTime, bpm));
  if (!visible.length) return null;
  return <div className="preview-effects" aria-hidden="true">{visible.map((effect) => {
    if (effect.type === 'cinematic_bars') return <div key={effect.id} className="preview-cinematic-bars" style={{ '--effect-color': effect.color || '#000000', '--effect-opacity': effect.opacity ?? .3, '--bar-size': `${1.8 + (Number(effect.intensity) || .05) * 12}%` }} />;
    if (!['twinkling_stars', 'sparkle_burst'].includes(effect.type)) return null;
    return <div key={effect.id} className={`preview-stars ${effect.type}`} style={{ color: effect.color || '#FFFFFF', opacity: Math.min(effect.opacity ?? .32, .65) }}>{seededPoints(effect).map((star) => <i key={star.id} style={{ left: `${star.left}%`, top: `${star.top}%`, fontSize: `${star.size}cqw`, animationDelay: `${star.delay}s`, animationDuration: `${star.speed}s` }}>✦</i>)}</div>;
  })}</div>;
}

function Toggle({ checked, onChange, label }) {
  return (
    <button type="button" className={`toggle ${checked ? 'active' : ''}`} role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}>
      <span />
    </button>
  );
}

function Range({ label, value, min = 0, max = 1, step = 0.05, suffix = '', onChange }) {
  const [displayValue, setDisplayValue] = useState(value);
  const frame = useRef(null);
  useEffect(() => setDisplayValue(value), [value]);
  useEffect(() => () => frame.current && cancelAnimationFrame(frame.current), []);
  const update = (event) => {
    const next = Number(event.currentTarget.value);
    setDisplayValue(next);
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => onChange(next));
  };
  const percentage = ((displayValue - min) / (max - min)) * 100;
  return (
    <label className="range-field">
      <span className="field-label"><span>{label}</span><output>{displayValue}{suffix}</output></span>
      <input style={{ '--range-progress': `${percentage}%` }} type="range" min={min} max={max} step={step} value={displayValue} onInput={update} onChange={update} />
    </label>
  );
}

function AssetField({ label, accept, value, onChange }) {
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append('file', file);
      const result = await api('/api/assets', { method: 'POST', body });
      onChange(result.path);
    } catch (error) {
      window.alert(error.message);
    } finally {
      setBusy(false);
      event.target.value = '';
    }
  };
  return (
    <div className="asset-field">
      <span className="field-label"><span>{label}</span></span>
      <div className="asset-input">
        <input value={value || ''} onChange={(event) => onChange(event.target.value)} placeholder="assets/file.png" />
        <button type="button" title={`Upload ${label}`} onClick={() => inputRef.current?.click()} disabled={busy}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
        </button>
      </div>
      <input ref={inputRef} className="hidden" type="file" accept={accept} onChange={upload} />
    </div>
  );
}

function VideoRow({ video, active, selected, deleting, stopping, onOpen, onToggle, onDelete, onCancel }) {
  const status = video.job?.status || 'idle';
  const busy = ACTIVE_JOB_STATUSES.includes(status);
  const progress = jobProgress(video.job);
  const sourceMetadata = video.source_metadata;
  return (
    <div className={`video-row ${active ? 'active' : ''}`} onClick={onOpen}>
      <button className={`check ${selected ? 'selected' : ''}`} type="button" aria-label={`${selected ? 'Unselect' : 'Select'} ${video.name}`} onClick={(event) => { event.stopPropagation(); onToggle(); }}>
        {selected && <Check size={13} strokeWidth={3} />}
      </button>
      <div className="video-thumb"><FileVideo size={20} /></div>
      <div className="video-info">
        <strong title={sourceMetadata?.title || video.relative_path}>{sourceMetadata?.title || video.name}</strong>
        <span>{formatDuration(video.duration)} · {formatBytes(video.size)}{sourceMetadata ? ' · YouTube metadata saved' : ''}</span>
        {sourceMetadata?.description && <small className="source-description" title={sourceMetadata.description}>{sourceMetadata.description}</small>}
        {busy && <span className="processing-copy">{status === 'queued' ? 'Waiting in queue · 0% complete' : status === 'cancelling' ? 'Stopping export…' : `${progress}% complete`}</span>}
        {status !== 'idle' && (
          <div className="row-progress">
            <i style={{ width: `${progress}%` }} className={status} />
          </div>
        )}
        {status === 'failed' && <small title={video.job.error}>{video.job.error || 'Processing failed'}</small>}
      </div>
      <span className={`status-pill ${status}`}>{status === 'processing' ? `${progress}%` : status === 'queued' ? '0%' : status}</span>
      <span className="video-row-actions">
        {busy && <button className="row-action cancel-job" type="button" disabled={stopping || status === 'cancelling'} aria-label={`Cancel ${video.job?.kind === 'export' ? 'export' : 'processing'} for ${video.name}`} title={`Cancel ${video.job?.kind === 'export' ? 'export' : 'processing'}`} onClick={(event) => { event.stopPropagation(); onCancel(); }}>
          {stopping || status === 'cancelling' ? <LoaderCircle className="spin" size={15} /> : <Square size={12} fill="currentColor" />}
        </button>}
        <button className="row-action danger" type="button" disabled={busy || deleting} aria-label={`Delete input video ${video.name}`} title={busy ? 'Cancel processing before deleting' : 'Delete input video'} onClick={(event) => { event.stopPropagation(); onDelete(); }}>
          {deleting ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
        </button>
      </span>
    </div>
  );
}

function OutputRow({ output, deleting, onDelete }) {
  return (
    <div className="output-row">
      <span className="output-icon"><PackageCheck size={19} /></span>
      <span className="output-copy">
        <strong>{output.source_metadata?.title || output.source || output.name}</strong>
        <small>{output.template ? `${output.template} · ` : ''}{formatDuration(output.duration)} · {formatBytes(output.size)} · {new Date(output.modified * 1000).toLocaleString()}</small>
        {output.source_metadata && <small>Original YouTube title and description preserved</small>}
        <time title={output.name}>{output.name}</time>
      </span>
      <a className="download-output" href={`${API}/api/outputs/${encodeURIComponent(output.name)}`} aria-label={`Download ${output.name}`} title={`Download ${output.name}`}><Download size={15} /><span>Download</span></a>
      <button className="row-action danger" type="button" disabled={deleting} aria-label={`Delete output video ${output.name}`} title="Delete output video" onClick={onDelete}>
        {deleting ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
      </button>
    </div>
  );
}

function ExportJobRow({ video, stopping, onStop }) {
  const status = video.job?.status || 'queued';
  const progress = jobProgress(video.job);
  return (
    <div className="output-row export-job-row" role="status" aria-label={`Exporting ${video.name}, ${progress}% complete`}>
      <span className="output-icon working"><LoaderCircle className="spin" size={19} /></span>
      <span className="output-copy">
        <strong>{video.name}</strong>
        <small>{video.job?.template || 'Video export'} · {video.job?.message || (status === 'queued' ? 'Waiting to start' : 'Rendering')}</small>
        <span className="export-job-progress"><i style={{ width: `${progress}%` }} /></span>
      </span>
      <span className={`status-pill ${status}`}>{status === 'queued' ? 'Queued' : status === 'cancelling' ? 'Stopping' : `${progress}%`}</span>
      <button className="stop-export" type="button" disabled={stopping || status === 'cancelling'} onClick={onStop} aria-label={`Stop export ${video.name}`}>
        {stopping || status === 'cancelling' ? <LoaderCircle className="spin" size={14} /> : <Square size={12} fill="currentColor" />}<span>{status === 'cancelling' ? 'Stopping' : 'Stop'}</span>
      </button>
    </div>
  );
}

function YouTubeDownloadRow({ job, stopping, retrying, onCancel, onRetry }) {
  const active = ACTIVE_DOWNLOAD_STATUSES.includes(job.status);
  const progress = Number(job.progress || 0);
  return (
    <div className={`youtube-job ${job.status}`} role="status">
      <span className="youtube-job-icon">{active ? <LoaderCircle className="spin" size={17} /> : job.status === 'completed' ? <Check size={17} /> : <Clapperboard size={17} />}</span>
      <span className="youtube-job-copy">
        <strong title={job.title}>{job.title || 'YouTube video'}</strong>
        <small>{job.message}{job.speed ? ` · ${job.speed}` : ''}{job.eta && job.eta !== 'NA' ? ` · ${job.eta} remaining` : ''}</small>
        {active && <span className="youtube-job-track"><i style={{ width: `${progress}%` }} /></span>}
        {job.status === 'failed' && <small className="youtube-job-error" title={job.error}>{job.error || 'Download failed'}</small>}
      </span>
      <span className={`status-pill ${job.status}`}>{active ? `${Math.round(progress)}%` : job.status}</span>
      {active && <button className="row-action cancel-job" type="button" disabled={stopping || job.status === 'cancelling'} onClick={onCancel} aria-label={`Cancel download ${job.title}`} title="Cancel download">{stopping || job.status === 'cancelling' ? <LoaderCircle className="spin" size={14} /> : <Square size={11} fill="currentColor" />}</button>}
      {job.status === 'failed' && <button className="row-action retry-download" type="button" disabled={retrying} onClick={onRetry} aria-label={`Retry download ${job.title}`} title="Retry download">{retrying ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}</button>}
      {job.status === 'completed' && job.source_metadata && <details className="youtube-job-metadata">
        <summary>Saved title and description</summary>
        <label><span>Title</span><input readOnly value={job.source_metadata.title || ''} /></label>
        <label><span>Description</span><textarea readOnly rows="4" value={job.source_metadata.description || ''} /></label>
      </details>}
    </div>
  );
}

function TimelineClip({ label, icon: Icon, start, end, duration, color, laneRef, onChange, snapPoints = [] }) {
  const drag = useRef(null);
  const safeStart = Math.max(0, Math.min(start ?? 0, duration));
  const safeEnd = Math.max(safeStart + 0.04, Math.min(end ?? duration, duration));
  const begin = (event, mode) => {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      mode,
      pointerId: event.pointerId,
      originX: event.clientX,
      start: safeStart,
      end: safeEnd,
      width: laneRef.current?.getBoundingClientRect().width || 1,
    };
  };
  const move = (event) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const delta = ((event.clientX - drag.current.originX) / drag.current.width) * duration;
    const minimum = Math.min(0.25, duration);
    let nextStart = drag.current.start;
    let nextEnd = drag.current.end;
    if (drag.current.mode === 'move') {
      const length = drag.current.end - drag.current.start;
      nextStart = Math.max(0, Math.min(drag.current.start + delta, duration - length));
      nextEnd = nextStart + length;
    } else if (drag.current.mode === 'left') {
      nextStart = Math.max(0, Math.min(drag.current.start + delta, drag.current.end - minimum));
    } else {
      nextEnd = Math.min(duration, Math.max(drag.current.end + delta, drag.current.start + minimum));
    }
    const snap = (value) => {
      const closest = snapPoints.reduce((best, point) => Math.abs(point - value) < Math.abs(best - value) ? point : best, value);
      return Math.abs(closest - value) <= Math.max(.08, duration / 500) ? closest : value;
    };
    if (drag.current.mode === 'left') nextStart = snap(nextStart);
    else if (drag.current.mode === 'right') nextEnd = snap(nextEnd);
    else {
      const snapped = snap(nextStart);
      nextEnd += snapped - nextStart;
      nextStart = snapped;
    }
    onChange({ start: Number(nextStart.toFixed(3)), end: Number(nextEnd.toFixed(3)) });
  };
  const finish = (event) => {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  };
  return (
    <div
      className="timeline-clip"
      style={{ left: `${(safeStart / duration) * 100}%`, width: `${((safeEnd - safeStart) / duration) * 100}%`, '--clip-color': color }}
      onPointerDown={(event) => begin(event, 'move')}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={finish}
      title={`${label}: ${safeStart.toFixed(1)}s–${safeEnd.toFixed(1)}s. Drag to move; use the edges to trim.`}
    >
      <button type="button" className="trim-handle left" aria-label={`Adjust ${label} start`} onPointerDown={(event) => begin(event, 'left')} />
      <span><Icon size={13} />{label}</span>
      <button type="button" className="trim-handle right" aria-label={`Adjust ${label} end`} onPointerDown={(event) => begin(event, 'right')} />
    </div>
  );
}

function Timeline({ video, draft, currentTime, onSeek, onTimingChange, beatAnalysis, clips = [], selectedClipId, onSelectClip, onSplit, onDeleteClip, onMoveClip, onUndo, onRedo, canUndo, canRedo }) {
  const [zoom, setZoom] = useState(1);
  const duration = Math.max(clips.length ? clips.reduce((sum, clip) => sum + Math.max(.04, Number(clip.out) - Number(clip.in)), 0) : (video?.duration || 15), 0.1);
  const laneRef = useRef(null);
  const labelsRef = useRef(null);
  const timelineScrollRef = useRef(null);
  const scrubbing = useRef(false);
  const layers = [
    { key: 'video', label: 'Video', icon: FileVideo, enabled: true, color: '#7c6cf3' },
    { key: 'text', label: 'Text', icon: Type, enabled: draft.text?.enabled, color: '#23b5d3', config: draft.text },
    ...(draft.animated_elements || []).map((element, index) => ({
      key: `animated-${element.id || index}`, elementId: element.id ?? index, label: element.text || `Animated ${index + 1}`,
      icon: Sparkles, enabled: element.enabled !== false, color: ['#f97316', '#06b6d4', '#eab308', '#ec4899'][index % 4], config: element,
    })),
    { key: 'logo_overlay', label: 'Logo', icon: Layers3, enabled: draft.logo_overlay?.enabled, color: '#f59e0b', config: draft.logo_overlay },
    { key: 'png_overlay', label: 'Overlay', icon: Layers3, enabled: draft.png_overlay?.enabled, color: '#ec4899', config: draft.png_overlay },
    { key: 'watermark', label: 'Watermark', icon: Layers3, enabled: draft.watermark?.enabled, color: '#10b981', config: draft.watermark },
    { key: 'background_music', label: 'Music', icon: Music2, enabled: draft.background_music?.enabled, color: '#8b5cf6', config: draft.background_music },
    { key: 'beat_track', label: 'Generated beat', icon: Music2, enabled: draft.beat_track?.enabled, color: '#22c55e', config: draft.beat_track },
    ...(draft.audio_tracks || []).map((track, index) => ({ key: `audio-track-${track.id}`, audioTrackId: track.id, label: track.name || `${track.type || 'Audio'} ${index + 1}`, icon: Music2, enabled: !!track.path && !track.muted, color: '#38bdf8', config: track })),
  ].filter((layer) => layer.enabled);
  const beatSeconds = 60 / Math.max(1, Number(draft.beat_track?.bpm) || 100);
  const manualBeats = Array.from({ length: Math.min(4000, Math.ceil(duration / beatSeconds) + 1) }, (_, index) => (Number(draft.beat_track?.offset) || 0) + index * beatSeconds).filter((time) => time >= 0 && time <= duration);
  const beats = beatAnalysis?.beats?.length ? beatAnalysis.beats : manualBeats;
  const downbeatSet = new Set((beatAnalysis?.downbeats || beats.filter((_, index) => index % 4 === 0)).map((time) => Number(time).toFixed(3)));
  const width = Math.max(720, duration * 34 * zoom);
  const tickCount = Math.min(13, Math.max(5, Math.ceil(duration / 5) + 1));
  const ticks = Array.from({ length: tickCount }, (_, index) => ({
    left: (index / (tickCount - 1)) * 100,
    time: (index / (tickCount - 1)) * duration,
  }));
  const seekFromPointer = (event) => {
    const rect = laneRef.current?.getBoundingClientRect();
    if (!rect) return;
    onSeek(Math.max(0, Math.min(duration, ((event.clientX - rect.left) / rect.width) * duration)));
  };
  const beginScrub = (event) => {
    if (event.target.closest('.timeline-clip')) return;
    scrubbing.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    seekFromPointer(event);
  };
  return (
    <section className="timeline-panel">
      <div className="timeline-toolbar">
        <div><strong>Timeline</strong><span>Split, trim and reorder source clips · overlay edges snap to beats</span></div>
        <div className="clip-tools"><button type="button" disabled={!canUndo} onClick={onUndo}>Undo</button><button type="button" disabled={!canRedo} onClick={onRedo}>Redo</button><button type="button" onClick={onSplit}>Split</button><button type="button" onClick={() => onMoveClip(-1)}>←</button><button type="button" onClick={() => onMoveClip(1)}>→</button><button className="danger" type="button" onClick={onDeleteClip}>Delete</button></div>
        <label className="timeline-zoom"><ZoomIn size={14} /><input type="range" min="0.6" max="2.5" step="0.1" value={zoom} onInput={(event) => setZoom(Number(event.currentTarget.value))} onChange={(event) => setZoom(Number(event.currentTarget.value))} /></label>
      </div>
      <div className="timeline-grid">
        <div
          className="track-labels"
          ref={labelsRef}
          onScroll={(event) => {
            if (timelineScrollRef.current) timelineScrollRef.current.scrollTop = event.currentTarget.scrollTop;
          }}
        >
          <div className="ruler-spacer" />
          {layers.map(({ key, label, icon: Icon }) => <div key={key} title={label}><Icon size={14} /><span>{label}</span></div>)}
        </div>
        <div
          className="timeline-scroll"
          ref={timelineScrollRef}
          onScroll={(event) => {
            if (labelsRef.current) labelsRef.current.scrollTop = event.currentTarget.scrollTop;
          }}
        >
          <div
            className="timeline-canvas"
            ref={laneRef}
            style={{ width }}
            onPointerDown={beginScrub}
            onPointerMove={(event) => scrubbing.current && seekFromPointer(event)}
            onPointerUp={() => { scrubbing.current = false; }}
            onPointerCancel={() => { scrubbing.current = false; }}
          >
            <div className="timeline-ruler">
              {ticks.map((tick) => <span key={tick.left} style={{ left: `${tick.left}%` }}>{formatDuration(tick.time)}</span>)}
              {draft.beat_track?.enabled && beats.map((time, index) => <i key={`${time}-${index}`} className={`beat-marker ${downbeatSet.has(Number(time).toFixed(3)) ? 'downbeat' : ''}`} style={{ left: `${(time / duration) * 100}%` }} title={`${downbeatSet.has(Number(time).toFixed(3)) ? 'Downbeat' : 'Beat'} ${index + 1} · ${time.toFixed(2)}s`} />)}
            </div>
            {!!beatAnalysis?.waveform?.length && <div className="waveform" aria-label="Audio waveform">{beatAnalysis.waveform.map((peak, index) => <i key={index} style={{ height: `${Math.max(4, peak * 100)}%` }} />)}</div>}
            {layers.map((layer) => (
              <div className="timeline-lane" key={layer.key}>
                {layer.key === 'video' ? (clips.length ? clips.map((clip, index) => {
                  const clipDuration = Math.max(.04, clip.out - clip.in);
                  const before = clips.slice(0, index).reduce((sum, item) => sum + Math.max(.04, item.out - item.in), 0);
                  return <button type="button" key={clip.id} className={`source-clip ${clip.id === selectedClipId ? 'selected' : ''}`} style={{ left: `${before / duration * 100}%`, width: `${clipDuration / duration * 100}%`, right: 'auto' }} onClick={(event) => { event.stopPropagation(); onSelectClip(clip.id); }} title={`${clip.name}: ${clip.in.toFixed(2)}s–${clip.out.toFixed(2)}s`}><FileVideo size={13} /><span>{clip.name || `Clip ${index + 1}`}</span></button>;
                }) : <div className="source-clip"><FileVideo size={13} /><span>{video?.name || 'Video clip'}</span></div>) : (
                  <TimelineClip
                    label={layer.label}
                    icon={layer.icon}
                    color={layer.color}
                    laneRef={laneRef}
                    duration={duration}
                    start={layer.config?.timing?.start ?? 0}
                    end={layer.config?.timing?.end ?? duration}
                    snapPoints={draft.beat_track?.enabled ? beats : []}
                    onChange={(timing) => onTimingChange(layer.key, timing, layer.elementId, layer.audioTrackId)}
                  />
                )}
              </div>
            ))}
            <div className="playhead" style={{ left: `${Math.min(100, (currentTime / duration) * 100)}%` }}><i /><span>{currentTime.toFixed(1)}s</span></div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Accordion({ title, icon: Icon, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`accordion ${open ? 'open' : ''}`}>
      <button type="button" className="accordion-title" onClick={() => setOpen(!open)}>
        <span><Icon size={17} />{title}</span><ChevronDown size={16} />
      </button>
      {open && <div className="accordion-body">{children}</div>}
    </section>
  );
}

function OverlayEditor({ label, config, update, accept = '.png,.jpg,.jpeg' }) {
  return (
    <div className="editor-group">
      <div className="inline-heading"><span>{label}</span><Toggle label={`Enable ${label}`} checked={!!config.enabled} onChange={(enabled) => update({ enabled })} /></div>
      {config.enabled && <>
        <AssetField label="Asset" accept={accept} value={config.path} onChange={(path) => update({ path })} />
        <div className="field-grid">
          <label><span>Position</span><select value={config.position || 'top-right'} onChange={(event) => update({ position: event.target.value })}>
            {['top-left','top-center','top-right','center-left','center','center-right','bottom-left','bottom-center','bottom-right'].map((position) => <option key={position}>{position}</option>)}
          </select></label>
          <label><span>Width</span><input type="number" min="24" max="1080" value={config.width || 180} onChange={(event) => update({ width: Number(event.target.value) })} /></label>
        </div>
        <Range label="Opacity" min={0} max={1} step={0.05} value={config.opacity ?? 1} onChange={(opacity) => update({ opacity })} />
      </>}
    </div>
  );
}

function AnimatedElementEditor({ element, index, update, remove }) {
  const timing = element.timing || { start: 0, end: null };
  return (
    <div className="animated-card">
      <div className="animated-card-header">
        <span><Sparkles size={14} /> Callout {index + 1}</span>
        <div><Toggle label={`Enable callout ${index + 1}`} checked={element.enabled !== false} onChange={(enabled) => update({ enabled })} /><button type="button" className="mini-danger" title="Delete callout" onClick={remove}><Trash2 size={14} /></button></div>
      </div>
      <label className="stacked"><span>Text</span><input value={element.text || ''} onChange={(event) => update({ text: event.target.value })} placeholder="DAY 1 · THE PLAN" /></label>
      <div className="field-grid">
        <label><span>Animation</span><select value={element.animation || 'fade'} onChange={(event) => update({ animation: event.target.value })}>{['fade','slide-left','slide-up','pulse'].map((animation) => <option key={animation} value={animation}>{animation}</option>)}</select></label>
        <label><span>Position</span><select value={element.position || 'top-center'} onChange={(event) => update({ position: event.target.value })}>{['top-left','top-center','top-right','center-left','center','center-right','bottom-left','bottom-center','bottom-right'].map((position) => <option key={position}>{position}</option>)}</select></label>
      </div>
      <div className="field-grid">
        <label><span>Starts at</span><input type="number" min="0" step="0.1" value={timing.start ?? 0} onChange={(event) => update({ timing: { ...timing, start: Number(event.target.value) } })} /></label>
        <label><span>Ends at</span><input type="number" min="0.1" step="0.1" value={timing.end ?? ''} placeholder="Video end" onChange={(event) => update({ timing: { ...timing, end: event.target.value === '' ? null : Number(event.target.value) } })} /></label>
      </div>
      <div className="field-grid">
        <label><span>Color</span><input value={element.color || '#ffffff'} onChange={(event) => update({ color: event.target.value })} /></label>
        <label><span>Font size</span><input type="number" min="16" max="240" value={element.font_size || 64} onChange={(event) => update({ font_size: Number(event.target.value) })} /></label>
      </div>
      <div className="inline-heading"><span>Dark text plate</span><Toggle label="Text background" checked={element.background !== false} onChange={(background) => update({ background })} /></div>
    </div>
  );
}

function AudioTrackEditor({ track, update, remove }) {
  return (
    <div className="animated-card audio-track-card">
      <div className="animated-card-header"><span><Music2 size={15} />{track.name || 'Audio track'}</span><button type="button" className="mini-danger" onClick={remove} aria-label="Remove audio track"><Trash2 size={14} /></button></div>
      <div className="field-grid"><label><span>Track type</span><select value={track.type || 'voiceover'} onChange={(event) => update({ type: event.target.value })}><option value="voiceover">Voiceover</option><option value="music">Music</option><option value="sfx">Sound effect</option></select></label><label><span>Name</span><input value={track.name || ''} onChange={(event) => update({ name: event.target.value })} /></label></div>
      <AssetField label="Audio file" accept="audio/*,.wav,.mp3,.m4a,.aac" value={track.path || ''} onChange={(path) => update({ path })} />
      <Range label="Track volume" min={0} max={2} step={.05} value={track.volume ?? 1} suffix="×" onChange={(volume) => update({ volume })} />
      <div className="field-grid"><label><span>Fade in (s)</span><input type="number" min="0" step=".05" value={track.fade_in || 0} onChange={(event) => update({ fade_in: Number(event.target.value) })} /></label><label><span>Fade out (s)</span><input type="number" min="0" step=".05" value={track.fade_out || 0} onChange={(event) => update({ fade_out: Number(event.target.value) })} /></label></div>
      <div className="track-switches"><span>Loop <Toggle label="Loop audio track" checked={!!track.loop} onChange={(loop) => update({ loop })} /></span><span>Mute <Toggle label="Mute audio track" checked={!!track.muted} onChange={(muted) => update({ muted })} /></span><span>Solo <Toggle label="Solo audio track" checked={!!track.solo} onChange={(solo) => update({ solo })} /></span></div>
    </div>
  );
}

function EditorApp({ automationEdit, onAutomationSaved }) {
  const [videos, setVideos] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [draft, setDraft] = useState(DEFAULT_TEMPLATE);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [activeVideoId, setActiveVideoId] = useState('');
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  const [notice, setNotice] = useState(null);
  const [outputs, setOutputs] = useState([]);
  const [libraryTab, setLibraryTab] = useState('media');
  const [currentTime, setCurrentTime] = useState(0);
  const [playerDuration, setPlayerDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playerVolume, setPlayerVolume] = useState(1);
  const [playerMuted, setPlayerMuted] = useState(false);
  const [batchIds, setBatchIds] = useState([]);
  const [processDialog, setProcessDialog] = useState(null);
  const [processTemplateId, setProcessTemplateId] = useState('');
  const [processStarting, setProcessStarting] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [automationSaving, setAutomationSaving] = useState(false);
  const [youtubeOpen, setYoutubeOpen] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [youtubeJobs, setYoutubeJobs] = useState([]);
  const [youtubeStarting, setYoutubeStarting] = useState(false);
  const [youtubeStopping, setYoutubeStopping] = useState(new Set());
  const [youtubeRetrying, setYoutubeRetrying] = useState(new Set());
  const [musicPreviewing, setMusicPreviewing] = useState(false);
  const [libraryCollapsed, setLibraryCollapsed] = useState(() => window.localStorage.getItem('shorts-library-collapsed') === 'true');
  const [deletingItems, setDeletingItems] = useState(new Set());
  const [stoppingJobs, setStoppingJobs] = useState(new Set());
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [projectName, setProjectName] = useState('Untitled project');
  const [beats, setBeats] = useState([]);
  const [beatGeneratorOpen, setBeatGeneratorOpen] = useState(false);
  const [beatGenerating, setBeatGenerating] = useState(false);
  const [beatGenerationJob, setBeatGenerationJob] = useState(null);
  const [beatForm, setBeatForm] = useState({ name: 'My Original Beat', bpm: 100, bars: 8, intensity: .55, swing: 0, density: .55, bass_level: .55, percussion_level: .65, seed: 1001 });
  const [analysisJob, setAnalysisJob] = useState(null);
  const [beatAnalysis, setBeatAnalysis] = useState(null);
  const [timelineClips, setTimelineClips] = useState([]);
  const [selectedClipId, setSelectedClipId] = useState('');
  const [timelineHistory, setTimelineHistory] = useState([]);
  const [timelineFuture, setTimelineFuture] = useState([]);
  const [pendingImageClip, setPendingImageClip] = useState('');
  const fileInput = useRef(null);
  const folderInput = useRef(null);
  const importInput = useRef(null);
  const videoRef = useRef(null);
  const phoneFrameRef = useRef(null);
  const musicPreviewRef = useRef(null);
  const previewTimerRef = useRef(null);
  const previewRequestRef = useRef(0);
  const tapTimesRef = useRef([]);
  const automationSelectionAppliedRef = useRef(false);

  const showNotice = (message, type = 'success') => {
    setNotice({ message, type });
    window.setTimeout(() => setNotice(null), 4200);
  };

  const refreshVideos = useCallback(async (quiet = false) => {
    try {
      const data = await api('/api/videos');
      setVideos(data);
      const automationVideoId = automationEdit?.editor_video_id;
      if (!automationSelectionAppliedRef.current && automationVideoId && data.some((video) => video.id === automationVideoId)) {
        automationSelectionAppliedRef.current = true;
        setActiveVideoId(automationVideoId);
        setSelectedIds(new Set([automationVideoId]));
      } else {
        setActiveVideoId((current) => current || data[0]?.id || '');
      }
    } catch (error) {
      if (!quiet) showNotice(error.message, 'error');
    } finally {
      setLoading(false);
    }
  }, [automationEdit?.editor_video_id]);

  const refreshTemplates = useCallback(async (preferredId) => {
    try {
      const data = await api('/api/templates');
      setTemplates(data.filter((item) => !item.invalid));
      const target = data.find((item) => item.id === preferredId) || data.find((item) => item.id === selectedTemplateId) || data[0];
      if (target) {
        setSelectedTemplateId(target.id);
        setDraft({ ...DEFAULT_TEMPLATE, ...target });
      } else {
        setSelectedTemplateId('');
        setDraft(structuredClone(DEFAULT_TEMPLATE));
      }
    } catch (error) { showNotice(error.message, 'error'); }
  }, [selectedTemplateId]);

  const refreshOutputs = useCallback(async (quiet = false) => {
    try { setOutputs(await api('/api/outputs')); }
    catch (error) { if (!quiet) showNotice(error.message, 'error'); }
  }, []);

  const refreshYoutubeDownloads = useCallback(async (quiet = false) => {
    try { setYoutubeJobs(await api('/api/youtube-downloads')); }
    catch (error) { if (!quiet) showNotice(error.message, 'error'); }
  }, []);

  const refreshProjects = useCallback(async () => {
    try { setProjects(await api('/api/projects')); }
    catch (error) { showNotice(error.message, 'error'); }
  }, []);

  const refreshBeats = useCallback(async () => {
    try { setBeats(await api('/api/beats')); }
    catch (error) { showNotice(error.message, 'error'); }
  }, []);

  useEffect(() => { refreshVideos(); refreshTemplates(automationEdit?.template_id); refreshOutputs(); refreshYoutubeDownloads(); refreshProjects(); refreshBeats(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => refreshVideos(true), 1800);
    return () => window.clearInterval(timer);
  }, [refreshVideos]);
  useEffect(() => {
    const timer = window.setInterval(() => refreshYoutubeDownloads(true), 1200);
    return () => window.clearInterval(timer);
  }, [refreshYoutubeDownloads]);
  useEffect(() => window.localStorage.setItem('shorts-library-collapsed', String(libraryCollapsed)), [libraryCollapsed]);
  useEffect(() => {
    if (!analysisJob || !['queued', 'analyzing', 'cancelling'].includes(analysisJob.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await api(`/api/beat-analysis/${analysisJob.id}`);
        setAnalysisJob(next);
        if (next.status === 'completed') {
          setBeatAnalysis(next.result);
          patchSection('beat_track', { enabled: true, bpm: next.result.bpm, offset: next.result.offset });
          showNotice(`Beat grid detected at ${next.result.bpm.toFixed(1)} BPM`);
        } else if (next.status === 'failed') showNotice(next.error || 'Beat analysis failed', 'error');
      } catch (error) { showNotice(error.message, 'error'); }
    }, 900);
    return () => window.clearInterval(timer);
  }, [analysisJob?.id, analysisJob?.status]);
  useEffect(() => {
    if (!beatGenerationJob || !['queued', 'generating', 'cancelling'].includes(beatGenerationJob.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await api(`/api/beats/generation/${beatGenerationJob.id}`);
        setBeatGenerationJob(next);
        if (next.status === 'completed') {
          const generated = next.result;
          await refreshBeats();
          patchSection('beat_track', { enabled: true, asset: generated.asset, bpm: generated.bpm, offset: 0, stems: generated.stems, use_stems: true, stem_levels: { kick: 1, snare: 1, percussion: 1, bass: 1, atmosphere: 1 } });
          setBeatGenerating(false);
          setBeatGeneratorOpen(false);
          showNotice(`Generated deterministic ${generated.bpm} BPM beat with five stems`);
        } else if (next.status === 'failed') {
          setBeatGenerating(false);
          showNotice(next.error || 'Beat generation failed', 'error');
        } else if (next.status === 'cancelled') {
          setBeatGenerating(false);
          showNotice('Beat generation cancelled');
        }
      } catch (error) {
        setBeatGenerating(false);
        showNotice(error.message, 'error');
      }
    }, 350);
    return () => window.clearInterval(timer);
  }, [beatGenerationJob?.id, beatGenerationJob?.status]);

  const activeVideo = videos.find((video) => video.id === activeVideoId);
  const selectedProfile = OUTPUT_PROFILES.find((profile) => profile.id === draft.output_profile?.id) || OUTPUT_PROFILES[2];
  const outputWidth = selectedProfile.id === 'preserve-source' ? (activeVideo?.width || 1920) : selectedProfile.width;
  const outputHeight = selectedProfile.id === 'preserve-source' ? (activeVideo?.height || 1080) : selectedProfile.height;
  const activeVideoBusy = ACTIVE_JOB_STATUSES.includes(activeVideo?.job?.status);
  const completed = videos.filter((video) => video.job?.status === 'completed').length;
  const processing = videos.filter((video) => ACTIVE_JOB_STATUSES.includes(video.job?.status)).length;
  const activeExportJobs = videos.filter((video) => ACTIVE_JOB_STATUSES.includes(video.job?.status));
  const activeYoutubeJobs = youtubeJobs.filter((job) => ACTIVE_DOWNLOAD_STATUSES.includes(job.status));
  const visibleOutputs = outputs.filter((output) => !activeExportJobs.some((video) => video.job?.target_output === output.name));
  const selectedHasBusyVideo = videos.some((video) => selectedIds.has(video.id) && ACTIVE_JOB_STATUSES.includes(video.job?.status));
  const trackedProgressVideos = (batchIds.length
    ? batchIds.map((id) => videos.find((video) => video.id === id)).filter(Boolean)
    : videos.filter((video) => ACTIVE_JOB_STATUSES.includes(video.job?.status)));
  const batchProgress = trackedProgressVideos.length
    ? Math.round(trackedProgressVideos.reduce((total, video) => total + (['completed', 'failed', 'cancelled'].includes(video.job?.status) ? 100 : jobProgress(video.job)), 0) / trackedProgressVideos.length)
    : 0;

  useEffect(() => {
    if (!processing && libraryTab !== 'exports') return undefined;
    refreshOutputs(true);
    const timer = window.setInterval(() => refreshOutputs(true), 1800);
    return () => window.clearInterval(timer);
  }, [processing, libraryTab, refreshOutputs]);

  useEffect(() => {
    if (completed > 0) refreshOutputs(true);
  }, [completed, refreshOutputs]);

  useEffect(() => {
    if (!batchIds.length) return;
    const batchVideos = batchIds.map((id) => videos.find((video) => video.id === id)).filter(Boolean);
    const finished = batchVideos.length === batchIds.length && batchVideos.every((video) => ['completed', 'failed', 'cancelled'].includes(video.job?.status));
    if (!finished) return;
    const failed = batchVideos.filter((video) => video.job?.status === 'failed').length;
    refreshOutputs();
    setBatchIds([]);
    showNotice(failed ? `Batch finished with ${failed} failed item${failed === 1 ? '' : 's'}.` : 'Batch finished. Your processed videos are ready.', failed ? 'error' : 'success');
  }, [videos, batchIds, refreshOutputs]);

  useEffect(() => {
    window.clearTimeout(previewTimerRef.current);
    previewRequestRef.current += 1;
    setCurrentTime(0);
    setPlayerDuration(activeVideo?.duration || 0);
    setIsPlaying(false);
    setPreviewBusy(false);
    setPreviewUrl('');
    setAnalysisJob(null);
    setBeatAnalysis(null);
    const firstClip = activeVideo ? [{ id: globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}`, name: activeVideo.name, path: '', in: 0, out: activeVideo.duration, enabled: true, transition: 'cut' }] : [];
    setTimelineClips(firstClip);
    setSelectedClipId(firstClip[0]?.id || '');
    setTimelineHistory([]);
    setTimelineFuture([]);
  }, [activeVideoId]);

  useEffect(() => () => window.clearTimeout(previewTimerRef.current), []);

  useEffect(() => {
    if (!videoRef.current) return;
    videoRef.current.volume = playerVolume;
    videoRef.current.muted = playerMuted;
  }, [playerVolume, playerMuted, previewUrl, activeVideoId]);

  useEffect(() => {
    if (!musicPreviewRef.current) return;
    musicPreviewRef.current.volume = Math.max(0, Math.min(1, Number(draft.background_music?.volume) || 0));
  }, [draft.background_music?.volume, draft.background_music?.path]);

  useEffect(() => {
    if (musicPreviewRef.current) {
      musicPreviewRef.current.pause();
      musicPreviewRef.current.currentTime = 0;
    }
    setMusicPreviewing(false);
  }, [draft.background_music?.path]);

  const patchSection = (section, change) => setDraft((current) => ({ ...current, [section]: { ...(current[section] || {}), ...change } }));
  const toggleMixerFlag = (flag, channel) => setDraft((current) => {
    const values = new Set(current.audio_mixer?.[flag] || []);
    values.has(channel) ? values.delete(channel) : values.add(channel);
    return { ...current, audio_mixer: { ...(current.audio_mixer || {}), [flag]: [...values] } };
  });
  const commitTimeline = (next) => {
    setTimelineHistory((history) => [...history.slice(-39), structuredClone(timelineClips)]);
    setTimelineFuture([]);
    setTimelineClips(next);
  };
  const undoTimeline = () => {
    if (!timelineHistory.length) return;
    const previous = timelineHistory.at(-1);
    setTimelineFuture((future) => [structuredClone(timelineClips), ...future].slice(0, 40));
    setTimelineHistory((history) => history.slice(0, -1));
    setTimelineClips(previous);
  };
  const redoTimeline = () => {
    if (!timelineFuture.length) return;
    const next = timelineFuture[0];
    setTimelineHistory((history) => [...history, structuredClone(timelineClips)].slice(-40));
    setTimelineFuture((future) => future.slice(1));
    setTimelineClips(next);
  };
  const updateTimelineClip = (id, change) => commitTimeline(timelineClips.map((clip) => clip.id === id ? { ...clip, ...change } : clip));
  const splitTimelineClip = () => {
    let cursor = 0;
    const index = timelineClips.findIndex((clip) => {
      const length = Math.max(.04, clip.out - clip.in);
      const contains = currentTime > cursor + .04 && currentTime < cursor + length - .04;
      cursor += length;
      return contains;
    });
    if (index < 0) return showNotice('Move the playhead inside a clip before splitting', 'error');
    const clip = timelineClips[index];
    const timelineStart = timelineClips.slice(0, index).reduce((sum, item) => sum + Math.max(.04, item.out - item.in), 0);
    const sourceSplit = clip.in + currentTime - timelineStart;
    const left = { ...clip, id: globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}-a`, out: Number(sourceSplit.toFixed(3)) };
    const right = { ...clip, id: globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}-b`, in: Number(sourceSplit.toFixed(3)) };
    const next = [...timelineClips.slice(0, index), left, right, ...timelineClips.slice(index + 1)];
    commitTimeline(next);
    setSelectedClipId(right.id);
  };
  const deleteTimelineClip = () => {
    if (timelineClips.length <= 1) return showNotice('A project needs at least one video clip', 'error');
    const index = timelineClips.findIndex((clip) => clip.id === selectedClipId);
    const next = timelineClips.filter((clip) => clip.id !== selectedClipId);
    commitTimeline(next);
    setSelectedClipId(next[Math.max(0, index - 1)]?.id || '');
  };
  const moveTimelineClip = (direction) => {
    const index = timelineClips.findIndex((clip) => clip.id === selectedClipId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= timelineClips.length) return;
    const next = [...timelineClips];
    [next[index], next[target]] = [next[target], next[index]];
    commitTimeline(next);
  };
  const addMediaClip = (videoId) => {
    const video = videos.find((item) => item.id === videoId);
    if (!video) return;
    const clip = { id: globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}`, name: video.name, path: video.relative_path, in: 0, out: video.duration, enabled: true, transition: 'cut' };
    commitTimeline([...timelineClips, clip]);
    setSelectedClipId(clip.id);
  };
  const addImageClip = () => {
    if (!pendingImageClip) return;
    const name = pendingImageClip.replaceAll('\\', '/').split('/').pop();
    const clip = { id: globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}`, name, path: pendingImageClip, in: 0, out: 3, duration: 3, enabled: true, transition: 'cut', kind: 'image' };
    commitTimeline([...timelineClips, clip]);
    setSelectedClipId(clip.id);
    setPendingImageClip('');
  };
  const updateAnimatedElement = (id, change) => setDraft((current) => ({
    ...current,
    animated_elements: (current.animated_elements || []).map((element, index) => (element.id ?? index) === id ? { ...element, ...change } : element),
  }));
  const addAnimatedElement = () => {
    const id = globalThis.crypto?.randomUUID?.() || `element-${Date.now()}`;
    setDraft((current) => ({
      ...current,
      animated_elements: [...(current.animated_elements || []), {
        id, enabled: true, text: 'NEW BUILD MILESTONE', animation: 'slide-up', position: 'top-center',
        font_size: 60, color: '#FFD166', background: true, margin: 90, timing: { start: 0, end: 4 },
      }],
    }));
  };
  const removeAnimatedElement = (id) => setDraft((current) => ({
    ...current, animated_elements: (current.animated_elements || []).filter((element, index) => (element.id ?? index) !== id),
  }));
  const addBeatEffect = (type = 'punch_zoom') => setDraft((current) => ({
    ...current,
    beat_effects: [...(current.beat_effects || []), {
      id: globalThis.crypto?.randomUUID?.() || `beat-effect-${Date.now()}`, type, enabled: true,
      trigger: type === 'twinkling_stars' ? 'every_n_beats' : 'downbeat', every_n_beats: type === 'twinkling_stars' ? 8 : 1,
      duration_ms: type === 'twinkling_stars' ? 700 : 220, intensity: type === 'twinkling_stars' ? .12 : .04, start: 0, end: null,
      opacity: type === 'twinkling_stars' ? .32 : .18, color: type === 'cinematic_bars' ? '#000000' : '#FFFFFF', position: 'center', beat_offset: 0, minimum_interval_ms: 400, seed: 1001,
    }],
  }));
  const updateBeatEffect = (id, change) => setDraft((current) => ({
    ...current, beat_effects: (current.beat_effects || []).map((effect) => effect.id === id ? { ...effect, ...change } : effect),
  }));
  const removeBeatEffect = (id) => setDraft((current) => ({ ...current, beat_effects: (current.beat_effects || []).filter((effect) => effect.id !== id) }));
  const addAudioTrack = () => setDraft((current) => ({ ...current, audio_tracks: [...(current.audio_tracks || []), {
    id: globalThis.crypto?.randomUUID?.() || `audio-${Date.now()}`, name: 'Voiceover', type: 'voiceover', path: '',
    volume: 1, loop: false, muted: false, solo: false, fade_in: 0, fade_out: 0, timing: { start: 0, end: null },
  }] }));
  const updateAudioTrack = (id, change) => setDraft((current) => ({ ...current, audio_tracks: (current.audio_tracks || []).map((track) => track.id === id ? { ...track, ...change } : track) }));
  const removeAudioTrack = (id) => setDraft((current) => ({ ...current, audio_tracks: (current.audio_tracks || []).filter((track) => track.id !== id) }));

  const selectTemplate = (id) => {
    const template = templates.find((item) => item.id === id);
    if (template) {
      window.clearTimeout(previewTimerRef.current);
      previewRequestRef.current += 1;
      setSelectedTemplateId(id);
      setDraft({ ...structuredClone(DEFAULT_TEMPLATE), ...structuredClone(template) });
      setPreviewBusy(false);
      setPreviewUrl('');
    }
  };

  const uploadVideos = async (event) => {
    const files = [...(event.target.files || [])].filter((file) => file.name.toLowerCase().endsWith('.mp4'));
    if (!files.length) return;
    setUploading(true);
    try {
      const body = new FormData();
      files.forEach((file) => body.append('files', file, file.name));
      const result = await api('/api/videos/upload', { method: 'POST', body });
      showNotice(`Added ${result.saved.length} video${result.saved.length === 1 ? '' : 's'}`);
      await refreshVideos();
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setUploading(false); event.target.value = ''; }
  };

  const startYoutubeDownload = async () => {
    const url = youtubeUrl.trim();
    if (!url) return showNotice('Paste a YouTube video URL', 'error');
    setYoutubeStarting(true);
    try {
      await api('/api/youtube-downloads', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }),
      });
      setYoutubeUrl('');
      await refreshYoutubeDownloads();
      showNotice('YouTube download added to the queue');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setYoutubeStarting(false); }
  };

  const cancelYoutubeDownload = async (job) => {
    if (!window.confirm(`Cancel downloading “${job.title || 'this video'}”? Partial files will be removed.`)) return;
    setYoutubeStopping((current) => new Set(current).add(job.id));
    try {
      await api(`/api/youtube-downloads/${job.id}/cancel`, { method: 'POST' });
      await refreshYoutubeDownloads();
      showNotice('Stopping YouTube download…');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setYoutubeStopping((current) => { const next = new Set(current); next.delete(job.id); return next; }); }
  };

  const retryYoutubeDownload = async (job) => {
    setYoutubeRetrying((current) => new Set(current).add(job.id));
    try {
      await api('/api/youtube-downloads', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: job.url }),
      });
      await refreshYoutubeDownloads();
      showNotice('YouTube download restarted with compatibility retries');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setYoutubeRetrying((current) => { const next = new Set(current); next.delete(job.id); return next; }); }
  };

  const saveTemplate = async () => {
    if (!draft.name.trim()) return showNotice('Give this preset a name', 'error');
    setSaving(true);
    const { id, name, ...settings } = draft;
    try {
      const method = selectedTemplateId ? 'PUT' : 'POST';
      const path = selectedTemplateId ? `/api/templates/${selectedTemplateId}` : '/api/templates';
      const saved = await api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, settings }) });
      await refreshTemplates(saved.id);
      showNotice('Preset saved');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setSaving(false); }
  };

  const importTemplate = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setImporting(true);
    try {
      const parsed = JSON.parse(await file.text());
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Preset JSON must contain one object');
      const name = String(parsed.name || file.name.replace(/\.json$/i, '')).trim();
      if (!name) throw new Error('Preset JSON needs a name');
      const sourceSettings = parsed.settings && typeof parsed.settings === 'object' && !Array.isArray(parsed.settings) ? parsed.settings : parsed;
      const { id: ignoredId, name: ignoredName, ...settings } = sourceSettings;
      if (!Object.keys(settings).length) throw new Error('Preset JSON does not contain any settings');
      const saved = await api('/api/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, settings }),
      });
      await refreshTemplates(saved.id);
      showNotice(`Imported “${name}”`);
    } catch (error) {
      showNotice(error instanceof SyntaxError ? 'That file is not valid JSON' : error.message, 'error');
    } finally {
      setImporting(false);
      event.target.value = '';
    }
  };

  const newTemplate = () => {
    window.clearTimeout(previewTimerRef.current);
    previewRequestRef.current += 1;
    setSelectedTemplateId('');
    setDraft({ ...structuredClone(DEFAULT_TEMPLATE), name: 'Untitled preset' });
    setPreviewBusy(false);
    setPreviewUrl('');
  };

  const saveProject = async () => {
    const name = projectName.trim();
    if (!name) return showNotice('Give this project a name', 'error');
    try {
      const data = {
        template_id: selectedTemplateId || null,
        video_id: activeVideoId || null,
        settings: draft,
        beat_analysis: beatAnalysis,
        timeline: { current_time: currentTime, clips: timelineClips, tracks: draft.timeline?.tracks || [] },
      };
      const path = selectedProjectId ? `/api/projects/${selectedProjectId}` : '/api/projects';
      const saved = await api(path, { method: selectedProjectId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, data }) });
      setSelectedProjectId(saved.id);
      await refreshProjects();
      showNotice('Project saved with cuts, timing and beat data');
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const openProject = async (identifier) => {
    if (!identifier) {
      setSelectedProjectId('');
      setProjectName('Untitled project');
      return;
    }
    try {
      const project = await api(`/api/projects/${identifier}`);
      setSelectedProjectId(project.id);
      setProjectName(project.name);
      if (project.settings) setDraft({ ...structuredClone(DEFAULT_TEMPLATE), ...project.settings });
      if (project.video_id && videos.some((video) => video.id === project.video_id)) setActiveVideoId(project.video_id);
      setBeatAnalysis(project.beat_analysis || null);
      if (project.timeline?.clips?.length) {
        setTimelineClips(project.timeline.clips);
        setSelectedClipId(project.timeline.clips[0].id || '');
      }
      showNotice(`Opened “${project.name}”`);
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const startBeatAnalysis = async () => {
    if (!activeVideoId) return showNotice('Choose a video to analyze', 'error');
    try {
      const job = await api(`/api/beat-analysis/${activeVideoId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: false }) });
      setAnalysisJob(job);
      showNotice('Beat analysis started locally');
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const cancelBeatAnalysis = async () => {
    if (!analysisJob) return;
    try {
      await api(`/api/beat-analysis/${analysisJob.id}/cancel`, { method: 'POST' });
      setAnalysisJob((current) => ({ ...current, status: 'cancelling', message: 'Stopping beat analysis' }));
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const generateBeat = async () => {
    setBeatGenerating(true);
    try {
      const job = await api('/api/beats/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(beatForm) });
      setBeatGenerationJob(job);
    } catch (error) { setBeatGenerating(false); showNotice(error.message, 'error'); }
  };

  const cancelBeatGeneration = async () => {
    if (!beatGenerationJob || !['queued', 'generating'].includes(beatGenerationJob.status)) return;
    try {
      const job = await api(`/api/beats/generation/${beatGenerationJob.id}/cancel`, { method: 'POST' });
      setBeatGenerationJob(job);
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const tapTempo = () => {
    const now = performance.now();
    const recent = [...tapTimesRef.current.filter((time) => now - time < 3000), now].slice(-8);
    tapTimesRef.current = recent;
    if (recent.length < 2) return;
    const intervals = recent.slice(1).map((time, index) => time - recent[index]);
    const bpm = Math.round(60000 / (intervals.reduce((sum, value) => sum + value, 0) / intervals.length));
    if (bpm >= 60 && bpm <= 180) patchSection('beat_track', { bpm });
  };

  const deleteTemplate = async () => {
    if (!selectedTemplateId || !window.confirm(`Delete “${draft.name}”?`)) return;
    try {
      await api(`/api/templates/${selectedTemplateId}`, { method: 'DELETE' });
      showNotice('Preset deleted');
      setSelectedTemplateId('');
      await refreshTemplates();
    } catch (error) { showNotice(error.message, 'error'); }
  };

  const openProcessDialog = (all = false) => {
    const videoIds = all ? videos.map((video) => video.id) : [...selectedIds];
    if (!videoIds.length) return showNotice(all ? 'Add at least one video first' : 'Select at least one video', 'error');
    if (!templates.length) return showNotice('Create or import a preset first', 'error');
    const initialTemplateId = templates.some((template) => template.id === selectedTemplateId) ? selectedTemplateId : templates[0].id;
    setProcessTemplateId(initialTemplateId);
    setProcessDialog({ mode: all ? 'all' : 'selected', videoIds });
  };

  const processSelection = async () => {
    if (!processDialog || !processTemplateId) return;
    const chosenTemplate = templates.find((template) => template.id === processTemplateId);
    if (!chosenTemplate) return showNotice('Choose an available preset', 'error');
    const all = processDialog.mode === 'all';
    const queuedIds = processDialog.videoIds;
    setProcessStarting(true);
    try {
      const result = await api('/api/process', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_id: processTemplateId, video_ids: all ? null : queuedIds }),
      });
      selectTemplate(processTemplateId);
      setProcessDialog(null);
      showNotice(`${result.queued} video${result.queued === 1 ? '' : 's'} queued with “${chosenTemplate.name}”`);
      setLibraryTab('media');
      await refreshVideos();
      setBatchIds(queuedIds);
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setProcessStarting(false); }
  };

  const openExport = () => {
    if (!activeVideo) return showNotice('Choose a video to export', 'error');
    window.clearTimeout(previewTimerRef.current);
    previewRequestRef.current += 1;
    setPreviewBusy(false);
    setExportOpen(true);
  };

  const exportActiveVideo = async () => {
    if (!activeVideoId || activeVideoBusy) return;
    setExporting(true);
    const { id, output: ignoredOutput, ...settings } = draft;
    try {
      const result = await api('/api/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: selectedTemplateId || 'draft',
          video_ids: [activeVideoId],
          settings: { ...settings, timeline: { ...(settings.timeline || {}), clips: timelineClips } },
          job_kind: 'export',
        }),
      });
      setBatchIds([activeVideoId]);
      setLibraryTab('exports');
      setExportOpen(false);
      await refreshVideos();
      showNotice('Export started at the video’s original frame rate. Track it in Exports.');
    } catch (error) {
      showNotice(error.message, 'error');
    } finally {
      setExporting(false);
    }
  };

  const saveAutomationVideo = async () => {
    if (!automationEdit?.render_job_id || !activeVideoId || automationSaving) return;
    setAutomationSaving(true);
    const { id, output: ignoredOutput, ...settings } = draft;
    try {
      await api(`/api/automation/render-jobs/${automationEdit.render_job_id}/save-edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { ...settings, timeline: { ...(settings.timeline || {}), clips: timelineClips } } }),
      });
      showNotice('Your edit was saved and queued as the new altered export.');
      window.setTimeout(() => onAutomationSaved?.(), 450);
    } catch (error) {
      showNotice(error.message, 'error');
    } finally {
      setAutomationSaving(false);
    }
  };

  const renderPreview = async ({ template = draft, quiet = false } = {}) => {
    if (!activeVideoId) return showNotice('Choose a video first', 'error');
    window.clearTimeout(previewTimerRef.current);
    const requestId = ++previewRequestRef.current;
    setPreviewBusy(true);
    try {
      const { id, output: ignoredOutput, ...settings } = template;
      const result = await api(`/api/preview/${activeVideoId}/${selectedTemplateId || 'draft'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { ...settings, timeline: { ...(settings.timeline || {}), clips: timelineClips } } }),
      });
      if (requestId !== previewRequestRef.current) return;
      setPreviewUrl(`${API}${result.url}?t=${Date.now()}`);
      if (!quiet) showNotice('Preview rendered with current editor settings');
    } catch (error) {
      if (requestId === previewRequestRef.current) showNotice(`Preview failed: ${error.message}`, 'error');
    } finally {
      if (requestId === previewRequestRef.current) setPreviewBusy(false);
    }
  };

  const openVideo = (id) => {
    window.clearTimeout(previewTimerRef.current);
    previewRequestRef.current += 1;
    setPreviewBusy(false);
    setPreviewUrl('');
    setCurrentTime(0);
    setIsPlaying(false);
    if (id === activeVideoId && videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
      videoRef.current.load();
    }
    setActiveVideoId(id);
  };

  const toggleSelection = (id) => {
    openVideo(id);
    setSelectedIds((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const setItemsDeleting = (keys, deleting) => setDeletingItems((current) => {
    const next = new Set(current);
    keys.forEach((key) => deleting ? next.add(key) : next.delete(key));
    return next;
  });

  const deleteInputVideo = async (video) => {
    if (!window.confirm(`Delete “${video.name}” from the input folder? This cannot be undone.`)) return;
    const key = `input:${video.id}`;
    setItemsDeleting([key], true);
    try {
      await api(`/api/videos/${video.id}`, { method: 'DELETE' });
      setSelectedIds((current) => { const next = new Set(current); next.delete(video.id); return next; });
      setActiveVideoId((current) => current === video.id ? (videos.find((item) => item.id !== video.id)?.id || '') : current);
      if (activeVideoId === video.id) setPreviewUrl('');
      await refreshVideos();
      showNotice('Input video deleted');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setItemsDeleting([key], false); }
  };

  const deleteSelectedInputs = async () => {
    const targets = videos.filter((video) => selectedIds.has(video.id));
    if (!targets.length || !window.confirm(`Delete ${targets.length} selected input video${targets.length === 1 ? '' : 's'}? This cannot be undone.`)) return;
    const keys = targets.map((video) => `input:${video.id}`);
    setItemsDeleting(keys, true);
    try {
      const results = await Promise.allSettled(targets.map((video) => api(`/api/videos/${video.id}`, { method: 'DELETE' })));
      const deletedIds = new Set(targets.filter((_, index) => results[index].status === 'fulfilled').map((video) => video.id));
      const failed = results.length - deletedIds.size;
      setSelectedIds((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
      setActiveVideoId((current) => deletedIds.has(current) ? (videos.find((video) => !deletedIds.has(video.id))?.id || '') : current);
      if (deletedIds.has(activeVideoId)) setPreviewUrl('');
      await refreshVideos();
      showNotice(failed ? `${deletedIds.size} deleted, ${failed} could not be deleted` : `${deletedIds.size} input video${deletedIds.size === 1 ? '' : 's'} deleted`, failed ? 'error' : 'success');
    } finally { setItemsDeleting(keys, false); }
  };

  const deleteOutputVideo = async (output) => {
    if (!window.confirm(`Delete “${output.name}” from the output folder? This cannot be undone.`)) return;
    const key = `output:${output.name}`;
    setItemsDeleting([key], true);
    try {
      await api(`/api/outputs/${encodeURIComponent(output.name)}`, { method: 'DELETE' });
      await refreshOutputs();
      showNotice('Output video deleted');
    } catch (error) { showNotice(error.message, 'error'); }
    finally { setItemsDeleting([key], false); }
  };

  const cancelJob = async (video, context = video.job?.kind === 'export' ? 'export' : 'processing') => {
    if (!window.confirm(`Cancel ${context} for “${video.name}”? The unfinished render will be removed.`)) return;
    setStoppingJobs((current) => new Set(current).add(video.id));
    try {
      await api(`/api/jobs/${video.id}/cancel`, { method: 'POST' });
      await refreshVideos();
      await refreshOutputs(true);
      showNotice(`Cancelling ${context}…`);
    } catch (error) {
      showNotice(error.message, 'error');
    } finally {
      setStoppingJobs((current) => { const next = new Set(current); next.delete(video.id); return next; });
    }
  };

  const cancelActiveBatch = async () => {
    const activeJobs = videos.filter((video) => ACTIVE_JOB_STATUSES.includes(video.job?.status));
    if (!activeJobs.length || !window.confirm(`Cancel all ${activeJobs.length} active processing job${activeJobs.length === 1 ? '' : 's'}? Unfinished renders will be removed.`)) return;
    const ids = activeJobs.map((video) => video.id);
    setStoppingJobs((current) => new Set([...current, ...ids]));
    try {
      const results = await Promise.allSettled(ids.map((id) => api(`/api/jobs/${id}/cancel`, { method: 'POST' })));
      const failed = results.filter((result) => result.status === 'rejected').length;
      await refreshVideos();
      await refreshOutputs(true);
      showNotice(failed ? `${ids.length - failed} jobs cancelling; ${failed} could not be cancelled` : 'Cancelling all active processing…', failed ? 'error' : 'success');
    } finally {
      setStoppingJobs((current) => { const next = new Set(current); ids.forEach((id) => next.delete(id)); return next; });
    }
  };

  const seekVideo = (seconds) => {
    setCurrentTime(seconds);
    if (videoRef.current) videoRef.current.currentTime = seconds;
  };

  const togglePlayback = () => {
    if (!videoRef.current) return;
    videoRef.current.paused ? videoRef.current.play() : videoRef.current.pause();
  };

  const togglePlayerMute = () => setPlayerMuted((current) => !current);

  const updatePlayerVolume = (event) => {
    const volume = Number(event.currentTarget.value);
    setPlayerVolume(volume);
    setPlayerMuted(volume === 0);
  };

  const enterFullscreen = () => phoneFrameRef.current?.requestFullscreen?.();

  const chooseBackgroundTrack = (path) => {
    if (path === 'custom') {
      patchSection('background_music', { enabled: true, path: '' });
      return;
    }
    const track = BACKGROUND_TRACKS.find((item) => item.path === path);
    if (track) patchSection('background_music', { enabled: true, path: track.path, volume: track.volume });
  };

  const toggleMusicPreview = async () => {
    const player = musicPreviewRef.current;
    if (!player) return;
    if (player.paused) {
      videoRef.current?.pause();
      try { await player.play(); }
      catch { showNotice('The browser could not play this audio track', 'error'); }
    } else {
      player.pause();
    }
  };

  const timingVisible = (config) => {
    const start = config?.timing?.start ?? 0;
    const end = config?.timing?.end ?? activeVideo?.duration ?? Infinity;
    return currentTime >= start && currentTime <= end;
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span><Clapperboard size={20} /></span><div><strong>YouTube Remix Studio</strong><small>Local video workspace</small></div></div>
        <div className="topbar-stats">
          <span><i className="dot ready" />{videos.length} videos</span>
          <span><i className={`dot ${processing ? 'working' : 'ready'}`} />{processing ? `${batchProgress}% complete · ${processing} remaining` : `${completed} finished`}</span>
        </div>
        <div className="local-badge"><span>LOCAL</span><strong>Docker workspace</strong></div>
        {automationEdit && <button className="automation-save-video" type="button" disabled={automationSaving || !activeVideoId} onClick={saveAutomationVideo}>{automationSaving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} {automationSaving ? 'Saving…' : 'Save video'}</button>}
      </header>

      <main className={`workspace ${libraryCollapsed ? 'library-collapsed' : ''}`}>
        <aside className={`library panel ${libraryCollapsed ? 'collapsed' : ''}`}>
          {libraryCollapsed ? (
            <div className="library-drawer-rail">
              <button className="icon-button drawer-toggle" type="button" title="Open Media drawer" aria-label="Open Media drawer" onClick={() => setLibraryCollapsed(false)}><PanelLeftOpen size={18} /></button>
              <span className="drawer-rail-label"><FileVideo size={16} /><strong>Media</strong><small>{videos.length}</small></span>
            </div>
          ) : <>
          <div className="panel-header">
            <div><p>YOUR CONTENT</p><h2>{libraryTab === 'media' ? 'Media' : 'Processed'}</h2></div>
            <div className="panel-header-actions">
              <button className="icon-button" type="button" title="Collapse Media drawer" aria-label="Collapse Media drawer" onClick={() => setLibraryCollapsed(true)}><PanelLeftClose size={17} /></button>
              <button className="icon-button" title="Refresh" onClick={() => { refreshVideos(); refreshOutputs(); }}><RefreshCw size={17} /></button>
            </div>
          </div>
          <div className="library-tabs" role="tablist" aria-label="Content library">
            <button className={libraryTab === 'media' ? 'active' : ''} onClick={() => setLibraryTab('media')}><FileVideo size={15} /> Media</button>
            <button className={libraryTab === 'exports' ? 'active' : ''} onClick={() => { setLibraryTab('exports'); refreshOutputs(); }}><PackageCheck size={15} /> Exports <span>{visibleOutputs.length}</span></button>
          </div>
          {libraryTab === 'media' ? <>
            <div className="upload-actions">
              <button className="primary compact" onClick={() => fileInput.current?.click()} disabled={uploading}>{uploading ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />} Upload MP4s</button>
              <button className="secondary compact" onClick={() => folderInput.current?.click()} disabled={uploading}><FolderOpen size={16} /> Choose folder</button>
              <button className="secondary compact youtube-import" type="button" onClick={() => { setYoutubeOpen(true); refreshYoutubeDownloads(); }}><Clapperboard size={16} /> Download from YouTube</button>
              <input ref={fileInput} className="hidden" type="file" accept="video/mp4,.mp4" multiple onChange={uploadVideos} />
              <input ref={folderInput} className="hidden" type="file" accept="video/mp4,.mp4" multiple webkitdirectory="" directory="" onChange={uploadVideos} />
            </div>
            {!!activeYoutubeJobs.length && <button className="youtube-active-summary" type="button" onClick={() => setYoutubeOpen(true)}>
              <span className="youtube-active-icon"><LoaderCircle className="spin" size={16} /></span>
              <span><strong>{activeYoutubeJobs.length} YouTube download{activeYoutubeJobs.length === 1 ? '' : 's'}</strong><small>{activeYoutubeJobs[0].title} · {Math.round(activeYoutubeJobs[0].progress || 0)}%</small></span>
              <span>{Math.round(activeYoutubeJobs[0].progress || 0)}%</span>
            </button>}
            <div className="library-summary">
              <button type="button" onClick={() => setSelectedIds(selectedIds.size === videos.length ? new Set() : new Set(videos.map((v) => v.id)))}>{selectedIds.size === videos.length && videos.length ? 'Clear all' : 'Select all'}</button>
              <span className="selection-actions"><span>{selectedIds.size} selected</span>{selectedIds.size > 0 && <button className="delete-selected" type="button" disabled={selectedHasBusyVideo} onClick={deleteSelectedInputs}><Trash2 size={13} /> Delete selected</button>}</span>
            </div>
            <div className="video-list">
              {loading && <div className="empty"><LoaderCircle className="spin" /><span>Scanning input folder…</span></div>}
              {!loading && !videos.length && <div className="empty"><FolderOpen /><strong>No MP4 videos yet</strong><span>Upload files or copy them into the mounted input folder.</span></div>}
              {videos.map((video) => <VideoRow key={video.id} video={video} active={video.id === activeVideoId} selected={selectedIds.has(video.id)} deleting={deletingItems.has(`input:${video.id}`)} stopping={stoppingJobs.has(video.id)} onOpen={() => openVideo(video.id)} onToggle={() => toggleSelection(video.id)} onDelete={() => deleteInputVideo(video)} onCancel={() => cancelJob(video)} />)}
            </div>
            <div className="batch-actions">
              {processing > 0 && (
                <div className="batch-progress-summary" role="status" aria-live="polite">
                  <div><span>Batch progress</span><span className="batch-progress-actions"><strong>{batchProgress}% complete</strong><button type="button" onClick={cancelActiveBatch}><Square size={10} fill="currentColor" /> Cancel all</button></span></div>
                  <div className="batch-progress-track" aria-hidden="true"><i style={{ width: `${batchProgress}%` }} /></div>
                  <small>{processing} video{processing === 1 ? '' : 's'} remaining · Processing continues one by one</small>
                </div>
              )}
              <button className="secondary" disabled={!selectedIds.size || processing > 0} onClick={() => openProcessDialog(false)}><Play size={17} /> Process selected</button>
              <button className="primary" disabled={!videos.length || processing > 0} onClick={() => openProcessDialog(true)}><WandSparkles size={17} /> Process all</button>
            </div>
          </> : <>
            <div className="exports-summary"><PackageCheck size={17} /><span><strong>{visibleOutputs.length} finished export{visibleOutputs.length === 1 ? '' : 's'}</strong><small>{activeExportJobs.length ? `${activeExportJobs.length} currently rendering` : 'Saved in your output folder'}</small></span></div>
            <div className="output-list">
              {!!activeExportJobs.length && <div className="export-queue-heading"><LoaderCircle className="spin" size={14} /><span>ACTIVE EXPORT{activeExportJobs.length === 1 ? '' : 'S'}</span></div>}
              {activeExportJobs.map((video) => <ExportJobRow key={`job:${video.id}`} video={video} stopping={stoppingJobs.has(video.id)} onStop={() => cancelJob(video, 'export')} />)}
              {!visibleOutputs.length && !activeExportJobs.length && <div className="empty"><PackageCheck /><strong>No processed videos yet</strong><span>Finished batches will appear here with download links.</span></div>}
              {!!visibleOutputs.length && <div className="export-queue-heading completed"><PackageCheck size={14} /><span>FINISHED</span></div>}
              {visibleOutputs.map((output) => <OutputRow key={output.name} output={output} deleting={deletingItems.has(`output:${output.name}`)} onDelete={() => deleteOutputVideo(output)} />)}
            </div>
            <div className="batch-result-footer"><span>{processing ? `${processing} item${processing === 1 ? '' : 's'} still processing` : 'All processing complete'}</span><button className="secondary compact" onClick={() => setLibraryTab('media')}>Back to media</button></div>
          </>}
          </>}
        </aside>

        <section className="stage">
          <div className="stage-header">
            <div><p>LIVE PREVIEW</p><h1 title={activeVideo?.source_metadata?.title}>{activeVideo?.source_metadata?.title || activeVideo?.name || 'Select a video'}</h1></div>
            <div className="stage-actions">
              <span className={`preview-fps-status ${previewBusy ? 'is-rendering' : ''}`}>{previewBusy ? 'Rendering preview…' : `Original frame rate${activeVideo?.fps ? ` · ${activeVideo.fps} FPS` : ''}`}</span>
              <button className="secondary compact" disabled={!activeVideo || previewBusy} onClick={() => renderPreview()}>
                {previewBusy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />} Render 6s preview
              </button>
              <button className="primary compact export-trigger" disabled={!activeVideo || activeVideoBusy || processing > 0} onClick={openExport}>
                <Download size={16} /> Export video
              </button>
            </div>
          </div>
          <div className="preview-surround">
            <div className="preview-player">
            <div className={`phone-frame ${outputWidth > outputHeight ? 'landscape' : ''}`} ref={phoneFrameRef} style={{ aspectRatio: `${outputWidth} / ${outputHeight}` }}>
              {activeVideo ? (
                <video
                  ref={videoRef}
                  key={previewUrl || activeVideo.id}
                  playsInline
                  preload="auto"
                  poster={`${API}/api/videos/${activeVideo.id}/poster`}
                  src={previewUrl || `${API}/api/videos/${activeVideo.id}/stream`}
                  style={!previewUrl ? {
                    objectFit: draft.scale?.mode === 'contain' ? 'contain' : 'cover',
                    objectPosition: `${(draft.crop?.focal_x ?? .5) * 100}% ${(draft.crop?.focal_y ?? .5) * 100}%`,
                    transformOrigin: `${(draft.crop?.focal_x ?? .5) * 100}% ${(draft.crop?.focal_y ?? .5) * 100}%`,
                    transform: `scale(${draft.scale?.mode === 'contain' ? 1 : (draft.scale?.zoom || 1)}) scaleX(${draft.mirror?.horizontal ? -1 : 1}) scaleY(${draft.mirror?.vertical ? -1 : 1})`,
                    filter: draft.image_adjustments?.enabled ? `contrast(${draft.image_adjustments.contrast ?? 1}) brightness(${1 + (draft.image_adjustments.brightness ?? 0)}) saturate(${draft.image_adjustments.saturation ?? 1})` : undefined,
                  } : undefined}
                  onClick={togglePlayback}
                  onLoadedMetadata={(event) => {
                    setPlayerDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : (activeVideo?.duration || 0));
                    if (!previewUrl && event.currentTarget.currentTime === 0) event.currentTarget.currentTime = 0.001;
                  }}
                  onError={() => {
                    if (!previewUrl && !previewBusy) renderPreview({ quiet: true });
                  }}
                  onPlay={() => setIsPlaying(true)}
                  onPause={() => setIsPlaying(false)}
                  onEnded={() => setIsPlaying(false)}
                  onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                  onSeeked={(event) => setCurrentTime(event.currentTarget.currentTime)}
                />
              ) : <div className="preview-empty"><Play size={28} /><span>Pick a video from the library</span></div>}
              {!previewUrl && activeVideo && ['logo_overlay', 'png_overlay', 'watermark'].map((section) => {
                const config = draft[section];
                return config?.enabled && timingVisible(config) ? <PreviewOverlayLayer key={`${section}-${config.path}`} config={config} canvasWidth={outputWidth} canvasHeight={outputHeight} /> : null;
              })}
              {!previewUrl && activeVideo && draft.text?.enabled && timingVisible(draft.text) && <PreviewTextLayer config={draft.text} canvasWidth={outputWidth} canvasHeight={outputHeight} />}
              {!previewUrl && activeVideo && (draft.animated_elements || []).filter((element) => element.enabled !== false && timingVisible(element)).map((element, index) => (
                <PreviewTextLayer key={element.id || index} config={element} animated canvasWidth={outputWidth} canvasHeight={outputHeight} />
              ))}
              {!previewUrl && activeVideo && <PreviewBeatEffects effects={draft.beat_effects} currentTime={currentTime} bpm={draft.beat_track?.bpm} />}
              {!previewUrl && activeVideo && draft.decorative_frame?.enabled && (
                <div
                  className={`preview-frame frame-${draft.decorative_frame.style || 'minimal'}`}
                  style={{
                    '--frame-color': draft.decorative_frame.color || '#FFFFFF',
                    '--frame-size': `${Math.max(1, (draft.decorative_frame.thickness || 18) / 4)}px`,
                    '--frame-inset': `${Math.max(0, (draft.decorative_frame.inset || 0) / 4)}px`,
                    '--frame-opacity': draft.decorative_frame.opacity ?? 0.9,
                  }}
                />
              )}
            </div>
            <div className="player-controls" aria-label="Video controls">
              <button type="button" className="white-control" disabled={!activeVideo} aria-label={isPlaying ? 'Pause video' : 'Play video'} title={isPlaying ? 'Pause' : 'Play'} onClick={togglePlayback}>{isPlaying ? <Pause size={17} /> : <Play size={17} fill="currentColor" />}</button>
              <input
                className="player-scrubber"
                aria-label="Video position"
                type="range"
                min="0"
                max={Math.max(playerDuration, 0.1)}
                step="0.01"
                value={Math.min(currentTime, Math.max(playerDuration, 0.1))}
                style={{ '--player-progress': `${playerDuration ? Math.min(100, (currentTime / playerDuration) * 100) : 0}%` }}
                disabled={!activeVideo}
                onInput={(event) => seekVideo(Number(event.currentTarget.value))}
                onChange={(event) => seekVideo(Number(event.currentTarget.value))}
              />
              <span className="player-time">{formatDuration(currentTime)} / {formatDuration(playerDuration)}</span>
              <button type="button" className="white-control" disabled={!activeVideo} aria-label={playerMuted ? 'Unmute video' : 'Mute video'} title={playerMuted ? 'Unmute' : 'Mute'} onClick={togglePlayerMute}>{playerMuted || playerVolume === 0 ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
              <input className="player-volume-slider" aria-label="Player volume" type="range" min="0" max="1" step="0.05" value={playerVolume} style={{ '--player-progress': `${playerVolume * 100}%` }} onInput={updatePlayerVolume} onChange={updatePlayerVolume} />
              <button type="button" className="white-control" disabled={!activeVideo} aria-label="Enter fullscreen" title="Fullscreen" onClick={enterFullscreen}><Maximize2 size={17} /></button>
            </div>
            </div>
          </div>
          <Timeline
            video={activeVideo}
            draft={draft}
            currentTime={currentTime}
            onSeek={seekVideo}
            beatAnalysis={beatAnalysis}
            clips={timelineClips}
            selectedClipId={selectedClipId}
            onSelectClip={setSelectedClipId}
            onSplit={splitTimelineClip}
            onDeleteClip={deleteTimelineClip}
            onMoveClip={moveTimelineClip}
            onUndo={undoTimeline}
            onRedo={redoTimeline}
            canUndo={!!timelineHistory.length}
            canRedo={!!timelineFuture.length}
            onTimingChange={(section, timing, elementId, audioTrackId) => audioTrackId ? updateAudioTrack(audioTrackId, { timing }) : elementId !== undefined ? updateAnimatedElement(elementId, { timing }) : patchSection(section, { timing })}
          />
        </section>

        <aside className="inspector panel">
          <div className="panel-header inspector-heading"><div><p>PRESET</p><h2>Template editor</h2></div><button className="icon-button" title="New preset" onClick={newTemplate}><Plus size={18} /></button></div>
          <div className="preset-controls">
            <select value={selectedTemplateId} onChange={(event) => selectTemplate(event.target.value)}><option value="">New unsaved preset</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select>
            <input aria-label="Preset name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            <div className="project-controls">
              <select aria-label="Open project" value={selectedProjectId} onChange={(event) => openProject(event.target.value)}><option value="">New video project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>
              <input aria-label="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
              <button type="button" className="secondary" onClick={saveProject}><FolderOpen size={15} /> Save project</button>
            </div>
            <div className="preset-buttons">
              <button className="secondary" onClick={() => importInput.current?.click()} disabled={importing}>{importing ? <LoaderCircle className="spin" size={16} /> : <FileJson size={16} />} Import JSON</button>
              <button className="primary" type="button" onClick={saveTemplate} disabled={saving || !draft.name.trim()} title="Save preset">
                {saving ? <LoaderCircle className="spin" size={16} /> : <Check size={17} strokeWidth={2.8} />} {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="icon-button danger" title="Delete preset" disabled={!selectedTemplateId} onClick={deleteTemplate}><Trash2 size={16} /></button>
              <input ref={importInput} className="hidden" type="file" accept="application/json,.json" onChange={importTemplate} />
            </div>
          </div>

          <div className="editor-scroll">
            <Accordion title="Output profile" icon={Maximize2} defaultOpen>
              <label className="stacked"><span>Canvas and export size</span><select value={selectedProfile.id} onChange={(event) => {
                const profile = OUTPUT_PROFILES.find((item) => item.id === event.target.value) || OUTPUT_PROFILES[2];
                patchSection('output_profile', { ...profile, preserve_source_fps: true });
              }}>{OUTPUT_PROFILES.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}{profile.width ? ` · ${profile.width} × ${profile.height}` : ''}</option>)}</select></label>
              <div className="profile-summary"><Maximize2 size={16} /><span><strong>{outputWidth} × {outputHeight}</strong><small>{selectedProfile.aspect_ratio === 'source' ? 'Source aspect ratio' : selectedProfile.aspect_ratio} · source FPS preserved · H.264/AAC</small></span></div>
            </Accordion>
            <Accordion title={`Project clips (${timelineClips.length})`} icon={Clapperboard}>
              <div className="clip-add-row"><select defaultValue="" onChange={(event) => { addMediaClip(event.target.value); event.target.value = ''; }}><option value="">Add B-roll or another video…</option>{videos.map((video) => <option key={video.id} value={video.id}>{video.name}</option>)}</select></div>
              <div className="image-clip-add"><AssetField label="Image clip" accept="image/png,image/jpeg,.png,.jpg,.jpeg" value={pendingImageClip} onChange={setPendingImageClip} /><button type="button" className="secondary" disabled={!pendingImageClip} onClick={addImageClip}><Plus size={14} /> Add image for 3s</button></div>
              <div className="clip-editor-list">{timelineClips.map((clip, index) => <button type="button" key={clip.id} className={`clip-editor-card ${selectedClipId === clip.id ? 'selected' : ''}`} onClick={() => setSelectedClipId(clip.id)}><span><strong>{index + 1}. {clip.name}</strong><small>{Math.max(.04, clip.out - clip.in).toFixed(2)}s · {clip.transition || 'cut'}</small></span></button>)}</div>
              {timelineClips.find((clip) => clip.id === selectedClipId) && (() => {
                const clip = timelineClips.find((item) => item.id === selectedClipId);
                return <div className="clip-properties"><div className="field-grid"><label><span>Source in (s)</span><input type="number" min="0" step=".01" value={clip.in} onChange={(event) => updateTimelineClip(clip.id, { in: Math.min(Number(event.target.value), clip.out - .04) })} /></label><label><span>Source out (s)</span><input type="number" min={clip.in + .04} step=".01" value={clip.out} onChange={(event) => updateTimelineClip(clip.id, { out: Math.max(Number(event.target.value), clip.in + .04) })} /></label></div><label className="stacked"><span>Transition to next clip</span><select value={clip.transition || 'cut'} onChange={(event) => updateTimelineClip(clip.id, { transition: event.target.value })}><option value="cut">Cut</option><option value="fade">Fade</option><option value="dissolve">Dissolve</option></select></label>{clip.transition !== 'cut' && <Range label="Transition duration" min={.08} max={1.5} step={.02} suffix="s" value={clip.transition_duration || .3} onChange={(transition_duration) => updateTimelineClip(clip.id, { transition_duration })} />}<small>FFmpeg renders the ordered cut list and applies the selected transition into the next clip.</small></div>;
              })()}
            </Accordion>
            <Accordion title="Frame & crop" icon={Settings2} defaultOpen>
              <label className="stacked"><span>Fit mode</span><select value={draft.scale?.mode || 'cover'} onChange={(event) => patchSection('scale', { mode: event.target.value })}><option value="cover">Fill & crop</option><option value="contain">Fit with bars</option></select></label>
              <Range label="Zoom" min={1} max={2} step={0.05} value={draft.scale?.zoom || 1} suffix="×" onChange={(zoom) => patchSection('scale', { zoom })} />
              <Range label="Horizontal focus" min={0} max={1} step={0.05} value={draft.crop?.focal_x ?? .5} onChange={(focal_x) => patchSection('crop', { focal_x })} />
              <Range label="Vertical focus" min={0} max={1} step={0.05} value={draft.crop?.focal_y ?? .5} onChange={(focal_y) => patchSection('crop', { focal_y })} />
              <div className="editor-group">
                <div className="inline-heading"><span>Mirror video</span></div>
                <div className="mirror-options">
                  <div><span>Horizontal</span><Toggle label="Mirror video horizontally" checked={!!draft.mirror?.horizontal} onChange={(horizontal) => patchSection('mirror', { horizontal })} /></div>
                  <div><span>Vertical</span><Toggle label="Mirror video vertically" checked={!!draft.mirror?.vertical} onChange={(vertical) => patchSection('mirror', { vertical })} /></div>
                </div>
              </div>
              <div className="editor-group">
                <div className="inline-heading"><span>Decorative frame</span><Toggle label="Enable decorative frame" checked={!!draft.decorative_frame?.enabled} onChange={(enabled) => patchSection('decorative_frame', { enabled })} /></div>
                {draft.decorative_frame?.enabled && <>
                  <div className="field-grid">
                    <label><span>Style</span><select value={draft.decorative_frame.style || 'minimal'} onChange={(event) => patchSection('decorative_frame', { style: event.target.value })}><option value="minimal">Minimal</option><option value="double">Elegant double</option><option value="neon">Neon glow</option><option value="cinematic">Cinematic</option></select></label>
                    <label><span>Color</span><input className="color-input" type="color" value={draft.decorative_frame.color || '#FFFFFF'} onChange={(event) => patchSection('decorative_frame', { color: event.target.value })} /></label>
                  </div>
                  <Range label="Thickness" min={2} max={80} step={2} value={draft.decorative_frame.thickness ?? 18} suffix="px" onChange={(thickness) => patchSection('decorative_frame', { thickness })} />
                  <Range label="Inset" min={0} max={160} step={4} value={draft.decorative_frame.inset ?? 24} suffix="px" onChange={(inset) => patchSection('decorative_frame', { inset })} />
                  <Range label="Frame opacity" min={0.1} max={1} step={0.05} value={draft.decorative_frame.opacity ?? .9} onChange={(opacity) => patchSection('decorative_frame', { opacity })} />
                </>}
              </div>
            </Accordion>

            <Accordion title="Text" icon={Type}>
              <div className="inline-heading"><span>Show text</span><Toggle label="Enable text" checked={!!draft.text?.enabled} onChange={(enabled) => patchSection('text', { enabled })} /></div>
              {draft.text?.enabled && <>
                <label className="stacked"><span>Content</span><textarea rows="3" value={draft.text.value || ''} onChange={(event) => patchSection('text', { value: event.target.value })} /></label>
                <div className="field-grid"><label><span>Position</span><select value={draft.text.position} onChange={(event) => patchSection('text', { position: event.target.value })}>{['top-left','top-center','top-right','center-left','center','center-right','bottom-left','bottom-center','bottom-right'].map((position) => <option key={position}>{position}</option>)}</select></label><label><span>Color</span><input value={draft.text.color || 'white'} onChange={(event) => patchSection('text', { color: event.target.value })} /></label></div>
                <Range label="Font size" min={16} max={180} step={2} value={draft.text.font_size || 64} suffix="px" onChange={(font_size) => patchSection('text', { font_size })} />
                <div className="inline-heading"><span>Text background</span><Toggle label="Text background" checked={draft.text.background !== false} onChange={(background) => patchSection('text', { background })} /></div>
              </>}
            </Accordion>

            <Accordion title={`Animated elements (${(draft.animated_elements || []).length})`} icon={Sparkles}>
              <div className="animated-intro"><Sparkles size={16} /><span>Each callout is rendered by FFmpeg and appears as its own draggable timeline layer.</span></div>
              {(draft.animated_elements || []).map((element, index) => <AnimatedElementEditor key={element.id || index} element={element} index={index} update={(change) => updateAnimatedElement(element.id ?? index, change)} remove={() => removeAnimatedElement(element.id ?? index)} />)}
              <button type="button" className="secondary add-element" onClick={addAnimatedElement}><Plus size={16} /> Add animated callout</button>
            </Accordion>

            <Accordion title="Overlays" icon={Layers3}>
              <OverlayEditor label="Logo" config={draft.logo_overlay || {}} update={(change) => patchSection('logo_overlay', change)} />
              <OverlayEditor label="PNG overlay" config={draft.png_overlay || {}} update={(change) => patchSection('png_overlay', change)} />
              <OverlayEditor label="Watermark" config={draft.watermark || {}} update={(change) => patchSection('watermark', change)} />
            </Accordion>

            <Accordion title="Beat library & analysis" icon={Music2} defaultOpen={selectedProfile.id !== 'youtube-shorts'}>
              <div className="inline-heading"><span>Generated beat track</span><Toggle label="Enable generated beat" checked={!!draft.beat_track?.enabled} onChange={(enabled) => patchSection('beat_track', { enabled })} /></div>
              <div className="analysis-actions">
                <button type="button" className="secondary" disabled={!activeVideo || ['queued','analyzing','cancelling'].includes(analysisJob?.status)} onClick={startBeatAnalysis}>{analysisJob && ['queued','analyzing'].includes(analysisJob.status) ? <LoaderCircle className="spin" size={15} /> : <Gauge size={15} />} Analyze source audio</button>
                {analysisJob && ['queued','analyzing','cancelling'].includes(analysisJob.status) && <button type="button" className="icon-button danger" title="Cancel analysis" onClick={cancelBeatAnalysis}><Square size={11} fill="currentColor" /></button>}
                <button type="button" className="secondary" onClick={() => setBeatGeneratorOpen(true)}><Plus size={15} /> Generate original beat</button>
              </div>
              {analysisJob && <div className={`analysis-status ${analysisJob.status}`}><span>{analysisJob.message}</span><strong>{Math.round(analysisJob.progress || 0)}%</strong><i><b style={{ width: `${analysisJob.progress || 0}%` }} /></i></div>}
              {draft.beat_track?.enabled && <>
                <label className="stacked"><span>Original beat library</span><select value={draft.beat_track.asset || ''} onChange={(event) => {
                  const beat = beats.find((item) => item.asset === event.target.value);
                  patchSection('beat_track', { asset: event.target.value, bpm: beat?.bpm || draft.beat_track.bpm, stems: beat?.stems || {}, use_stems: !!beat?.stems, stem_levels: { kick: 1, snare: 1, percussion: 1, bass: 1, atmosphere: 1 } });
                }}><option value="">Choose a generated beat</option>{beats.map((beat) => <option key={`${beat.id}-${beat.asset}`} value={beat.asset}>{beat.name} · {beat.bpm} BPM</option>)}</select></label>
                {!!draft.beat_track?.asset && <audio className="beat-audio" controls loop src={assetPreviewUrl(draft.beat_track.asset)} />}
                <div className="field-grid"><label title="BPM controls the spacing of the beat grid."><span>BPM</span><input type="number" min="60" max="180" step=".1" value={draft.beat_track.bpm || 100} onChange={(event) => patchSection('beat_track', { bpm: Number(event.target.value) })} /></label><label title="Beat offset moves the whole beat grid relative to the video."><span>Beat offset (s)</span><input type="number" step=".01" value={draft.beat_track.offset || 0} onChange={(event) => patchSection('beat_track', { offset: Number(event.target.value) })} /></label></div>
                <button type="button" className="tap-tempo" onClick={tapTempo} title="Tap four or more times in rhythm to estimate BPM">Tap tempo</button>
                <Range label="Beat volume" min={0} max={1} step={.01} value={draft.beat_track.volume ?? .12} suffix="×" onChange={(volume) => patchSection('beat_track', { volume })} />
                {!!Object.keys(draft.beat_track?.stems || {}).length && <div className="stem-mixer"><div className="inline-heading"><span>Individual generated stems</span><Toggle label="Mix individual beat stems" checked={draft.beat_track.use_stems !== false} onChange={(use_stems) => patchSection('beat_track', { use_stems })} /></div>{Object.keys(draft.beat_track.stems).map((stem) => <Range key={stem} label={stem[0].toUpperCase() + stem.slice(1)} min={0} max={1.5} step={.05} value={draft.beat_track.stem_levels?.[stem] ?? 1} suffix="×" onChange={(value) => patchSection('beat_track', { stem_levels: { ...(draft.beat_track.stem_levels || {}), [stem]: value } })} />)}</div>}
                <div className="inline-heading"><span title="Lowers the beat while speech is present.">Duck beat under dialogue</span><Toggle label="Automatic beat ducking under dialogue" checked={draft.beat_track.ducking?.enabled !== false} onChange={(enabled) => patchSection('beat_track', { ducking: { ...(draft.beat_track.ducking || {}), enabled } })} /></div>
              </>}
            </Accordion>

            <Accordion title={`Beat effects (${(draft.beat_effects || []).length})`} icon={Sparkles}>
              <div className="inline-heading"><span>Reduced-flashing safety</span><Toggle label="Use reduced flashing" checked={draft.motion_safety?.reduced_flashing !== false} onChange={(reduced_flashing) => patchSection('motion_safety', { reduced_flashing })} /></div>
              {(draft.beat_effects || []).map((effect) => <div className="beat-effect-card" key={effect.id}>
                <div className="inline-heading"><span className="effect-type-control"><Toggle label={`Enable ${effect.type}`} checked={effect.enabled !== false} onChange={(enabled) => updateBeatEffect(effect.id, { enabled })} /><select value={effect.type} onChange={(event) => updateBeatEffect(effect.id, { type: event.target.value })}>{EFFECT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></span><button className="icon-button danger" type="button" onClick={() => removeBeatEffect(effect.id)}><Trash2 size={14} /></button></div>
                <div className="field-grid"><label title="Choose which beat events trigger the effect."><span>Trigger</span><select value={effect.trigger || 'downbeat'} onChange={(event) => updateBeatEffect(effect.id, { trigger: event.target.value })}><option value="every_beat">Every beat</option><option value="downbeat">Downbeat</option><option value="every_n_beats">Every N beats</option><option value="manual">Manual timing</option></select></label><label><span>Every N beats</span><input type="number" min="1" max="32" value={effect.every_n_beats || 1} onChange={(event) => updateBeatEffect(effect.id, { every_n_beats: Number(event.target.value) })} /></label></div>
                <Range label="Intensity" min={0} max={.5} step={.01} value={effect.intensity ?? .04} onChange={(intensity) => updateBeatEffect(effect.id, { intensity })} />
                <Range label="Duration" min={40} max={900} step={20} suffix="ms" value={effect.duration_ms || 160} onChange={(duration_ms) => updateBeatEffect(effect.id, { duration_ms })} />
                <Range label="Opacity" min={.02} max={1} step={.02} value={effect.opacity ?? .18} onChange={(opacity) => updateBeatEffect(effect.id, { opacity })} />
                <div className="field-grid"><label><span>Color</span><input type="color" value={/^#[0-9a-f]{6}$/i.test(effect.color || '') ? effect.color : '#FFFFFF'} onChange={(event) => updateBeatEffect(effect.id, { color: event.target.value })} /></label><label><span>Seed</span><input type="number" min="0" value={effect.seed ?? 1001} onChange={(event) => updateBeatEffect(effect.id, { seed: Number(event.target.value) })} /></label></div>
                <div className="field-grid"><label><span>Starts at</span><input type="number" min="0" step=".1" value={effect.start ?? 0} onChange={(event) => updateBeatEffect(effect.id, { start: Number(event.target.value) })} /></label><label><span>Ends at</span><input type="number" min="0" step=".1" value={effect.end ?? ''} placeholder="Video end" onChange={(event) => updateBeatEffect(effect.id, { end: event.target.value === '' ? null : Number(event.target.value) })} /></label></div>
              </div>)}
              <div className="effect-quick-add"><button type="button" onClick={() => addBeatEffect('twinkling_stars')}>✦ Stars</button><button type="button" onClick={() => addBeatEffect('sparkle_burst')}>✧ Sparkle</button><button type="button" onClick={() => addBeatEffect('cinematic_bars')}>▰ Bars</button></div>
              <button type="button" className="secondary add-element" onClick={() => addBeatEffect()}><Plus size={16} /> Add beat effect</button>
            </Accordion>

            <Accordion title="Audio" icon={Volume2}>
              <div className="mixer-heading"><Gauge size={17} /><span><strong>Audio mixer</strong><small>All tracks are mixed into the final AAC output.</small></span></div>
              <div className="mixer-switchboard">{[['original','Original'],['music','Uploaded music'],['beat','Generated beat']].map(([channel,label]) => <div key={channel}><span>{label}</span><button type="button" className={(draft.audio_mixer?.muted || []).includes(channel) ? 'active' : ''} onClick={() => toggleMixerFlag('muted', channel)}>M</button><button type="button" className={(draft.audio_mixer?.soloed || []).includes(channel) ? 'active solo' : ''} onClick={() => toggleMixerFlag('soloed', channel)}>S</button></div>)}</div>
              <div className="mixer-strip"><span className="mixer-icon source"><Volume2 size={15} /></span><Range label="Original video volume" min={0} max={2} step={0.05} value={draft.original_audio_volume ?? 1} suffix="×" onChange={(original_audio_volume) => setDraft((current) => ({ ...current, original_audio_volume }))} /></div>
              <div className="mixer-strip music">
                <span className="mixer-icon"><Music2 size={15} /></span>
                <div className="mixer-content">
                  <div className="inline-heading"><span>Background music</span><Toggle label="Enable background music" checked={!!draft.background_music?.enabled} onChange={(enabled) => patchSection('background_music', { enabled })} /></div>
                  {draft.background_music?.enabled && <>
                    <label className="stacked"><span>Music library</span><select value={BACKGROUND_TRACKS.some((track) => track.path === draft.background_music.path) ? draft.background_music.path : 'custom'} onChange={(event) => chooseBackgroundTrack(event.target.value)}>{BACKGROUND_TRACKS.map((track) => <option key={track.path} value={track.path}>{track.name} · {track.mood}</option>)}<option value="custom">Upload custom music…</option></select></label>
                    {!BACKGROUND_TRACKS.some((track) => track.path === draft.background_music.path) && <AssetField label="Custom music file" accept="audio/*,.mp3,.wav,.m4a,.aac" value={draft.background_music.path} onChange={(path) => patchSection('background_music', { path })} />}
                    {draft.background_music.path && <div className="music-audition"><audio ref={musicPreviewRef} src={assetPreviewUrl(draft.background_music.path)} preload="metadata" onPlay={() => setMusicPreviewing(true)} onPause={() => setMusicPreviewing(false)} onEnded={() => setMusicPreviewing(false)} /><button className="music-play" type="button" onClick={toggleMusicPreview} aria-label={musicPreviewing ? 'Pause music preview' : 'Play music preview'}>{musicPreviewing ? <Pause size={15} /> : <Play size={15} fill="currentColor" />}</button><span><strong>{BACKGROUND_TRACKS.find((track) => track.path === draft.background_music.path)?.name || 'Custom track'}</strong><small>{musicPreviewing ? 'Playing at the selected music level' : 'Preview background track'}</small></span></div>}
                    <Range label="Background music volume" min={0} max={2} step={0.05} value={draft.background_music.volume ?? .2} suffix="×" onChange={(volume) => patchSection('background_music', { volume })} />
                    <div className="field-grid"><label><span>Fade in (seconds)</span><input type="number" min="0" step="0.25" value={draft.background_music.fade_in ?? 1} onChange={(event) => patchSection('background_music', { fade_in: Number(event.target.value) })} /></label><label><span>Fade out (seconds)</span><input type="number" min="0" step="0.25" value={draft.background_music.fade_out ?? 2} onChange={(event) => patchSection('background_music', { fade_out: Number(event.target.value) })} /></label></div>
                  </>}
                </div>
              </div>
              <div className="mixer-strip master"><span className="mixer-icon"><Gauge size={15} /></span><Range label="Master mix volume" min={0} max={2} step={0.05} value={draft.master_audio_volume ?? 1} suffix="×" onChange={(master_audio_volume) => setDraft((current) => ({ ...current, master_audio_volume }))} /></div>
              <div className="inline-heading"><span title="Targets YouTube-friendly integrated loudness before peak limiting.">Loudness normalization</span><Toggle label="Normalize output loudness" checked={draft.audio_mixer?.normalize !== false} onChange={(normalize) => patchSection('audio_mixer', { normalize })} /></div>
              <div className="editor-group"><div className="inline-heading"><span>Voiceover, music & sound effects</span><button type="button" className="mini-add" onClick={addAudioTrack}><Plus size={13} /> Add track</button></div>{(draft.audio_tracks || []).map((track) => <AudioTrackEditor key={track.id} track={track} update={(change) => updateAudioTrack(track.id, change)} remove={() => removeAudioTrack(track.id)} />)}{!(draft.audio_tracks || []).length && <small className="empty-track-copy">Add independent tracks, then drag their timing clips on the timeline.</small>}</div>
            </Accordion>

            <Accordion title="Intro & outro" icon={Clapperboard}>
              {['intro','outro'].map((section) => <div className="editor-group" key={section}><div className="inline-heading"><span className="capitalize">{section}</span><Toggle label={`Enable ${section}`} checked={!!draft[section]?.enabled} onChange={(enabled) => patchSection(section, { enabled })} /></div>{draft[section]?.enabled && <><AssetField label={`${section} video`} accept="video/mp4,.mp4,.mov" value={draft[section].path} onChange={(path) => patchSection(section, { path })} /><Range label={`${section[0].toUpperCase()}${section.slice(1)} audio`} min={0} max={2} step={0.05} value={draft[section].volume ?? 1} suffix="×" onChange={(volume) => patchSection(section, { volume })} /></>}</div>)}
            </Accordion>
          </div>
        </aside>
      </main>
      {processDialog && (
        <div className="export-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !processStarting && setProcessDialog(null)}>
          <section className="export-dialog process-dialog" role="dialog" aria-modal="true" aria-labelledby="process-dialog-title">
            <div className="export-dialog-header process-dialog-header">
              <div className="process-dialog-title">
                <span className="process-dialog-mark"><WandSparkles size={20} /></span>
                <div><p>READY TO CREATE</p><h2 id="process-dialog-title">Choose a preset</h2></div>
              </div>
              <button className="icon-button" type="button" aria-label="Close processing dialog" disabled={processStarting} onClick={() => setProcessDialog(null)}><X size={17} /></button>
            </div>
            <div className="export-dialog-body process-dialog-body">
              <div className="process-batch-summary">
                <span className="process-batch-icon"><FileVideo size={19} /></span>
                <span><strong>{processDialog.videoIds.length} video{processDialog.videoIds.length === 1 ? '' : 's'}</strong><small>{processDialog.mode === 'all' ? 'All media in your input library' : 'Only your selected media'} · Processed one by one</small></span>
                <span className="process-batch-pill">{processDialog.mode === 'all' ? 'FULL BATCH' : 'SELECTION'}</span>
              </div>
              <fieldset className="process-preset-fieldset">
                <legend>Select the look for this batch</legend>
                <div className="process-preset-list">
                  {templates.map((template) => {
                    const animationCount = (template.animated_elements || []).filter((element) => element.enabled !== false).length;
                    const features = [
                      animationCount ? `${animationCount} animation${animationCount === 1 ? '' : 's'}` : null,
                      template.background_music?.enabled ? 'Music mix' : null,
                      template.decorative_frame?.enabled ? 'Frame' : null,
                      template.mirror?.horizontal || template.mirror?.vertical ? 'Mirror' : null,
                      template.image_adjustments?.enabled ? 'Color grade' : null,
                    ].filter(Boolean);
                    const selected = processTemplateId === template.id;
                    return (
                      <button key={template.id} className={`process-preset-card ${selected ? 'selected' : ''}`} type="button" role="radio" aria-checked={selected} onClick={() => setProcessTemplateId(template.id)}>
                        <span className="process-preset-icon"><Layers3 size={19} /></span>
                        <span className="process-preset-copy"><strong>{template.name}</strong><small>{features.length ? features.join(' · ') : 'Clean vertical video treatment'}</small></span>
                        <span className="process-preset-check">{selected && <Check size={15} strokeWidth={3} />}</span>
                      </button>
                    );
                  })}
                </div>
              </fieldset>
              <div className="process-note"><Sparkles size={16} /><span>The selected preset's output profile, source frame rate, H.264 video and AAC stereo audio will be used. Your original files stay unchanged.</span></div>
            </div>
            <div className="export-dialog-actions process-dialog-actions">
              <button className="secondary" type="button" disabled={processStarting} onClick={() => setProcessDialog(null)}>Cancel</button>
              <button className="primary process-confirm" type="button" disabled={processStarting || !processTemplateId} onClick={processSelection}>
                {processStarting ? <LoaderCircle className="spin" size={17} /> : <WandSparkles size={17} />}
                {processStarting ? 'Starting batch…' : `Process ${processDialog.videoIds.length} video${processDialog.videoIds.length === 1 ? '' : 's'}`}
              </button>
            </div>
          </section>
        </div>
      )}
      {youtubeOpen && (
        <div className="export-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !youtubeStarting && setYoutubeOpen(false)}>
          <section className="export-dialog youtube-dialog" role="dialog" aria-modal="true" aria-labelledby="youtube-dialog-title">
            <div className="export-dialog-header youtube-dialog-header">
              <div><p>IMPORT FROM YOUTUBE</p><h2 id="youtube-dialog-title">Download video to Media</h2></div>
              <button className="icon-button" type="button" aria-label="Close YouTube download dialog" disabled={youtubeStarting} onClick={() => setYoutubeOpen(false)}><X size={17} /></button>
            </div>
            <div className="export-dialog-body youtube-dialog-body">
              <label className="youtube-url-field">
                <span>YouTube video URL</span>
                <div><Clapperboard size={18} /><input type="url" value={youtubeUrl} onChange={(event) => setYoutubeUrl(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && !youtubeStarting && startYoutubeDownload()} placeholder="https://www.youtube.com/watch?v=…" autoFocus /></div>
              </label>
              <div className="youtube-permission-note"><CircleAlert size={16} /><span>Download only videos you own or have permission to use. Playlists are intentionally limited to one video.</span></div>
              {!!youtubeJobs.length && <div className="youtube-jobs-section">
                <div className="youtube-jobs-heading"><span>DOWNLOAD ACTIVITY</span><button type="button" onClick={() => refreshYoutubeDownloads()}><RefreshCw size={13} /> Refresh</button></div>
                <div className="youtube-jobs-list">
                  {youtubeJobs.slice(0, 6).map((job) => <YouTubeDownloadRow key={job.id} job={job} stopping={youtubeStopping.has(job.id)} retrying={youtubeRetrying.has(job.id)} onCancel={() => cancelYoutubeDownload(job)} onRetry={() => retryYoutubeDownload(job)} />)}
                </div>
              </div>}
            </div>
            <div className="export-dialog-actions">
              <button className="secondary" type="button" disabled={youtubeStarting} onClick={() => setYoutubeOpen(false)}>Close</button>
              <button className="primary youtube-download-start" type="button" disabled={youtubeStarting || !youtubeUrl.trim()} onClick={startYoutubeDownload}>{youtubeStarting ? <LoaderCircle className="spin" size={17} /> : <Clapperboard size={17} />} {youtubeStarting ? 'Adding…' : 'Download video'}</button>
            </div>
          </section>
        </div>
      )}
      {exportOpen && (
        <div className="export-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !exporting && setExportOpen(false)}>
          <section className="export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-dialog-title">
            <div className="export-dialog-header">
              <div><p>DOWNLOAD VIDEO</p><h2 id="export-dialog-title">Export edited video</h2></div>
              <button className="icon-button" type="button" aria-label="Close export dialog" disabled={exporting} onClick={() => setExportOpen(false)}><X size={17} /></button>
            </div>
            <div className="export-dialog-body">
            <div className="export-source"><FileVideo size={18} /><span><strong>{activeVideo?.name}</strong><small>MP4 · H.264 video · AAC stereo audio · {outputWidth} × {outputHeight}</small></span></div>
              <div className="export-note"><Gauge size={16} /><span>The export keeps the source video’s original frame rate{activeVideo?.fps ? ` (${activeVideo.fps} FPS)` : ''}. No frame generation or motion interpolation is applied, which substantially reduces processing time.</span></div>
            </div>
            <div className="export-dialog-actions">
              <button className="secondary" type="button" disabled={exporting} onClick={() => setExportOpen(false)}>Cancel</button>
              <button className="primary" type="button" disabled={exporting || activeVideoBusy} onClick={exportActiveVideo}>{exporting ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />} {exporting ? 'Starting export…' : 'Export video'}</button>
            </div>
          </section>
        </div>
      )}
      {beatGeneratorOpen && (
        <div className="export-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !beatGenerating && setBeatGeneratorOpen(false)}>
          <section className="export-dialog beat-generator-dialog" role="dialog" aria-modal="true" aria-labelledby="beat-generator-title">
            <div className="export-dialog-header"><div><p>ORIGINAL AUDIO</p><h2 id="beat-generator-title">Generate a reusable beat</h2></div><button className="icon-button" disabled={beatGenerating} onClick={() => setBeatGeneratorOpen(false)}><X size={17} /></button></div>
            <div className="export-dialog-body beat-generator-body">
              <div className="youtube-permission-note"><Music2 size={16} /><span>Generated locally from synthesis only—no copyrighted melodies, recordings, or samples. The same seed and settings reproduce the same loop.</span></div>
              <label className="stacked"><span>Beat name</span><input value={beatForm.name} onChange={(event) => setBeatForm({ ...beatForm, name: event.target.value })} /></label>
              <div className="field-grid"><label><span>BPM</span><input type="number" min="60" max="180" value={beatForm.bpm} onChange={(event) => setBeatForm({ ...beatForm, bpm: Number(event.target.value) })} /></label><label><span>Bars</span><input type="number" min="1" max="32" value={beatForm.bars} onChange={(event) => setBeatForm({ ...beatForm, bars: Number(event.target.value) })} /></label></div>
              <Range label="Intensity" value={beatForm.intensity} onChange={(intensity) => setBeatForm((current) => ({ ...current, intensity }))} />
              <Range label="Swing" min={0} max={.45} step={.01} value={beatForm.swing} onChange={(swing) => setBeatForm((current) => ({ ...current, swing }))} />
              <Range label="Density" value={beatForm.density} onChange={(density) => setBeatForm((current) => ({ ...current, density }))} />
              <Range label="Bass level" value={beatForm.bass_level} onChange={(bass_level) => setBeatForm((current) => ({ ...current, bass_level }))} />
              <Range label="Percussion level" value={beatForm.percussion_level} onChange={(percussion_level) => setBeatForm((current) => ({ ...current, percussion_level }))} />
              <label className="stacked"><span>Deterministic seed</span><input type="number" min="0" value={beatForm.seed} onChange={(event) => setBeatForm({ ...beatForm, seed: Number(event.target.value) })} /></label>
              {beatGenerationJob && beatGenerating && <div className={`analysis-status ${beatGenerationJob.status}`}><span>{beatGenerationJob.message}</span><strong>{Math.round(beatGenerationJob.progress || 0)}%</strong><i><b style={{ width: `${beatGenerationJob.progress || 0}%` }} /></i></div>}
            </div>
            <div className="export-dialog-actions"><button className="secondary" onClick={beatGenerating ? cancelBeatGeneration : () => setBeatGeneratorOpen(false)}>{beatGenerating ? 'Stop generation' : 'Cancel'}</button><button className="primary" disabled={beatGenerating || !beatForm.name.trim()} onClick={generateBeat}>{beatGenerating ? <LoaderCircle className="spin" size={17} /> : <Music2 size={17} />}{beatGenerating ? 'Synthesizing stems…' : 'Generate beat'}</button></div>
          </section>
        </div>
      )}
      {notice && <div className={`toast ${notice.type}`}>{notice.type === 'error' ? <CircleAlert size={18} /> : <Check size={18} />}{notice.message}<button onClick={() => setNotice(null)}><X size={15} /></button></div>}
    </div>
  );
}

function automationEditFromHash() {
  if (!window.location.hash.startsWith('#editor?')) return null;
  const params = new URLSearchParams(window.location.hash.slice(window.location.hash.indexOf('?') + 1));
  const renderJobId = params.get('render_job_id');
  const editorVideoId = params.get('editor_video_id');
  if (!renderJobId || !editorVideoId) return null;
  return { render_job_id: renderJobId, editor_video_id: editorVideoId, template_id: params.get('template_id') || '', title: params.get('title') || 'Automation video' };
}

function dashboardSectionFromHash() {
  const section = window.location.hash.slice(1).split('?')[0];
  return ['overview', 'channels', 'new-batch', 'source-feeds', 'queue', 'history'].includes(section) ? section : 'overview';
}

function App() {
  const [surface, setSurface] = useState(() => window.location.hash.startsWith('#editor') ? 'editor' : 'automation');
  const [automationEdit, setAutomationEdit] = useState(automationEditFromHash);
  const [dashboardSection, setDashboardSection] = useState(() => automationEditFromHash() ? 'queue' : dashboardSectionFromHash());
  useEffect(() => {
    const syncHash = () => {
      const context = automationEditFromHash();
      setAutomationEdit(context);
      if (context) setDashboardSection('queue');
      else setDashboardSection(dashboardSectionFromHash());
      setSurface(window.location.hash.startsWith('#editor') ? 'editor' : 'automation');
    };
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);
  const openSurface = (next) => {
    window.location.hash = next === 'editor' ? 'editor' : '';
    setSurface(next);
  };
  const openAdvancedEditor = () => { setAutomationEdit(null); openSurface('editor'); };
  const returnToDashboard = () => { setAutomationEdit(null); openSurface('automation'); };
  if (surface === 'editor') return <div className="editor-app-wrap"><button className="editor-dashboard-return" onClick={returnToDashboard}><LayoutDashboard size={16} /> {automationEdit ? 'Back to work queue' : 'Automation dashboard'}</button><EditorApp automationEdit={automationEdit} onAutomationSaved={returnToDashboard} /></div>;
  return <AutomationDashboard initialSection={dashboardSection} onOpenEditor={openAdvancedEditor} />;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
