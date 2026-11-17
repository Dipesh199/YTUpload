import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowRight, CalendarClock, Check, ChevronRight, CircleDot,
  Clapperboard, Clock3, Download, ExternalLink, FileClock, History, LayoutDashboard, Link2,
  ListChecks, LoaderCircle, LockKeyhole, Menu, Pause, Pencil, Play, Plus, Radio, RefreshCw,
  RotateCcw, Rss, Settings2, ShieldCheck, Trash2, Unplug, UploadCloud, Video, X,
} from 'lucide-react';
import './automation.css';

const API = import.meta.env.VITE_API_URL || '';

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* empty body */ }
    throw new Error(message);
  }
  return response.json();
}

const navItems = [
  ['overview', LayoutDashboard, 'Overview'],
  ['channels', Radio, 'Channels'],
  ['new-batch', Link2, 'New URL Batch'],
  ['source-feeds', Rss, 'Source channels'],
  ['queue', ListChecks, 'Work queue'],
  ['history', History, 'History'],
];

const prettyState = (value) => value === 'scheduled' ? 'scheduled public' : String(value || 'unknown').replaceAll('_', ' ');
const dateTime = (value) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';
const publicationDateTime = (value) => value ? new Intl.DateTimeFormat(undefined, {
  year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
}).format(new Date(value)) : '—';
const dateTimeInputValue = (value) => {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};
const defaultScheduledFor = () => {
  const date = new Date(Date.now() + 60 * 60_000);
  date.setSeconds(0, 0);
  const remainder = date.getMinutes() % 30;
  if (remainder) date.setMinutes(date.getMinutes() + 30 - remainder);
  return date.toISOString();
};

function StatusPill({ value }) {
  const tone = ['completed', 'connected', 'published', 'scheduled', 'accepted', 'ready', 'rendered', 'rendered_awaiting_upload'].includes(value)
    ? 'good'
    : ['invalid', 'failed', 'blocked_not_connected', 'download_failed', 'render_failed', 'upload_failed', 'processing_failed', 'post_publish_failed', 'channel_mismatch', 'reauthorization_required'].includes(value)
      ? 'bad'
      : ['duplicate', 'paused', 'awaiting_channel_connection', 'authorization_pending', 'pending_adoption', 'waiting_for_source', 'waiting_for_render', 'waiting_for_upload', 'acquiring', 'downloading', 'rendering', 'validating', 'uploading', 'youtube_processing', 'publishing', 'retry_requested', 'edit_requested'].includes(value)
        ? 'warn'
        : 'neutral';
  return <span className={`auto-status ${tone}`}><i />{prettyState(value)}</span>;
}

function EmptyState({ icon: Icon, title, children, action }) {
  return <div className="auto-empty"><span><Icon size={22} /></span><strong>{title}</strong><p>{children}</p>{action}</div>;
}

function UploadCountdown({ scheduledFor, intervalMinutes = 15, scheduled = false }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const remaining = Math.max(0, Math.floor((new Date(scheduledFor).getTime() - now) / 1000));
  const total = Math.max(1, intervalMinutes * 60);
  const progress = Math.max(0, Math.min(100, ((total - remaining) / total) * 100));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const seconds = remaining % 60;
  const value = `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return <div className="upload-countdown" style={{ '--countdown-progress': `${progress * 3.6}deg` }}><div><small>{remaining ? scheduled ? 'PUBLISHES IN' : 'UPLOADS IN' : scheduled ? 'RELEASE TIME' : 'READY TO UPLOAD'}</small><strong>{value}</strong><span>{intervalMinutes}-minute {scheduled ? 'release spacing' : 'upload cadence'}</span></div></div>;
}

function NextUploadCard({ upload, onViewQueue }) {
  if (!upload) return <section className="next-upload-card empty"><div><span className="eyebrow">PUBLISHING QUEUE</span><h2>No rendered video waiting</h2><p>Create a URL batch and each completed scheduled export will upload as soon as the publisher is free.</p></div><button className="auto-button primary" onClick={onViewQueue}>View queue</button></section>;
  const scheduled = upload.visibility === 'scheduled';
  return <section className="next-upload-card">
    <div className="next-upload-preview"><video controls preload="metadata" poster={upload.thumbnail_url || undefined} src={`${API}${upload.video_url}`} /></div>
    <div className="next-upload-copy"><div className="next-upload-kicker"><span><CalendarClock size={14} /> {scheduled ? 'NEXT PUBLIC RELEASE' : 'NEXT UPLOAD'}</span><StatusPill value={upload.state} /></div><h2>{upload.title}</h2><p>{upload.description}</p><div className="next-upload-meta"><span><strong>{upload.channel.display_name}</strong><small>Destination</small></span><span><strong>{dateTime(upload.scheduled_for)}</strong><small>{scheduled ? 'YouTube release' : 'Upload slot'}</small></span><button onClick={onViewQueue}>Open queue <ArrowRight size={14} /></button></div></div>
    <UploadCountdown scheduledFor={upload.scheduled_for} intervalMinutes={upload.interval_minutes} scheduled={scheduled} />
  </section>;
}

function Overview({ data, loading, onNavigate, onRefresh }) {
  const itemStates = data?.counts?.items_by_state || {};
  const queued = ['queued', 'downloading', 'rendering', 'validating'].reduce((total, state) => total + (itemStates[state] || 0), 0);
  const channels = data?.counts?.channels || 0;
  const batches = data?.counts?.batches || 0;
  return <>
    <div className="auto-page-heading">
      <div><span className="eyebrow">OPERATIONS</span><h1>Good morning</h1><p>Your durable automation control plane is online.</p></div>
      <button className="auto-button ghost" onClick={onRefresh} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh</button>
    </div>
    <div className="metric-grid">
      <article><span className="metric-icon blue"><Radio size={19} /></span><div><small>Destination channels</small><strong>{channels}</strong><span>{channels ? 'Registered locally' : 'Add your first destination'}</span></div></article>
      <article><span className="metric-icon violet"><Link2 size={19} /></span><div><small>URL batches</small><strong>{batches}</strong><span>Persisted in PostgreSQL</span></div></article>
      <article><span className="metric-icon amber"><Clock3 size={19} /></span><div><small>Active items</small><strong>{queued}</strong><span>Download and remix worker</span></div></article>
    </div>
    <NextUploadCard upload={data?.next_upload} onViewQueue={() => onNavigate('queue')} />
    <div className="auto-two-column">
      <section className="auto-card">
        <div className="auto-card-head"><div><span className="eyebrow">RECENT WORK</span><h2>URL batches</h2></div><button onClick={() => onNavigate('queue')}>View queue <ArrowRight size={14} /></button></div>
        {!data?.recent_batches?.length ? <EmptyState icon={Link2} title="No batches yet" action={<button className="auto-button primary" onClick={() => onNavigate('new-batch')}><Plus size={15} /> Create URL batch</button>}>Select a destination, validate YouTube links, and save your first immutable batch.</EmptyState> :
          <div className="batch-list">{data.recent_batches.map((batch) => <button key={batch.id} className="batch-row" onClick={() => onNavigate('queue')}><span className="batch-avatar">{batch.destination_channel.display_name.slice(0, 2).toUpperCase()}</span><span><strong>{batch.name}</strong><small>{batch.destination_channel.display_name} · {batch.accepted_count} accepted</small></span><StatusPill value={batch.state} /><ChevronRight size={17} /></button>)}</div>}
      </section>
      <section className="auto-card">
        <div className="auto-card-head"><div><span className="eyebrow">SYSTEM</span><h2>Readiness</h2></div></div>
        <div className="readiness-list">
          <div><span className="check-ring ready"><Check size={14} /></span><span><strong>PostgreSQL & migrations</strong><small>Durable schema ready</small></span></div>
          <div><span className="check-ring ready"><Check size={14} /></span><span><strong>Idempotency records</strong><small>Render and publish keys reserved</small></span></div>
          <div><span className="check-ring ready"><Check size={14} /></span><span><strong>Acquisition & remix worker</strong><small>Downloading and altered exports active</small></span></div>
          <div><span className={`check-ring ${data?.oauth_configured ? 'ready' : 'pending'}`}>{data?.oauth_configured ? <Check size={14} /> : <Clock3 size={14} />}</span><span><strong>Google OAuth</strong><small>{data?.oauth_configured ? 'Client credentials loaded securely' : 'Client credentials required'}</small></span></div>
          <div><span className={`check-ring ${data?.publishing_enabled ? 'ready' : 'pending'}`}>{data?.publishing_enabled ? <Check size={14} /> : <Clock3 size={14} />}</span><span><strong>YouTube publisher</strong><small>{data?.publishing_enabled ? 'Resumable uploads and processing checks active' : 'Publishing disabled by configuration'}</small></span></div>
        </div>
      </section>
    </div>
  </>;
}

function Channels({ channels, oauthConfigured, onCreated }) {
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [working, setWorking] = useState('');
  const [error, setError] = useState('');
  const [callback, setCallback] = useState(() => {
    const query = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
    const params = new URLSearchParams(query);
    return { status: params.get('oauth'), code: params.get('code'), channel: params.get('channel') };
  }, []);
  const [form, setForm] = useState({ display_name: '', youtube_channel_id: '', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', daily_upload_limit: 5, upload_interval_minutes: 15 });
  const submit = async (event) => {
    event.preventDefault(); setSaving(true); setError('');
    try {
      await request('/api/automation/channels', { method: 'POST', body: JSON.stringify(form) });
      setAdding(false); setForm({ ...form, display_name: '', youtube_channel_id: '' }); await onCreated();
    } catch (caught) { setError(caught.message); } finally { setSaving(false); }
  };
  const connect = async (channel) => {
    setWorking(channel.id); setError('');
    try {
      const result = await request(`/api/automation/channels/${channel.id}/oauth/start`, { method: 'POST' });
      window.location.assign(result.authorization_url);
    } catch (caught) { setError(caught.message); setWorking(''); }
  };
  const verify = async (channel) => {
    setWorking(channel.id); setError('');
    try { await request(`/api/automation/channels/${channel.id}/oauth/verify`, { method: 'POST' }); await onCreated(); }
    catch (caught) { setError(caught.message); } finally { setWorking(''); }
  };
  const disconnect = async (channel) => {
    if (!window.confirm(`Disconnect ${channel.display_name} from Google? Pending videos will remain saved and will not upload.`)) return;
    setWorking(channel.id); setError('');
    try { await request(`/api/automation/channels/${channel.id}/oauth`, { method: 'DELETE' }); await onCreated(); }
    catch (caught) { setError(caught.message); } finally { setWorking(''); }
  };
  const removeChannel = async (channel) => {
    if (!window.confirm(`Remove ${channel.display_name} from active destinations? Existing batches and history will be preserved.`)) return;
    setWorking(channel.id); setError('');
    try { await request(`/api/automation/channels/${channel.id}`, { method: 'DELETE' }); await onCreated(); }
    catch (caught) { setError(caught.message); } finally { setWorking(''); }
  };
  const adoptCandidate = async (channel, candidate) => {
    if (!window.confirm(`Use ${candidate.channel_title} (${candidate.authorized_channel_id}) as the YouTube channel for ${channel.display_name}? The destination record and its existing batches will be preserved.`)) return;
    setWorking(channel.id); setError('');
    try {
      await request(`/api/automation/channels/${channel.id}/oauth/candidates/${candidate.id}/adopt`, { method: 'POST' });
      window.history.replaceState(null, '', '#channels?oauth=success&channel=' + encodeURIComponent(candidate.channel_title));
      setCallback({ status: 'success', code: null, channel: candidate.channel_title });
      await onCreated();
    } catch (caught) { setError(caught.message); } finally { setWorking(''); }
  };
  const discardCandidate = async (channel, candidate) => {
    if (!window.confirm(`Discard the authorization for ${candidate.channel_title}? Its pending credentials will be revoked or removed.`)) return;
    setWorking(channel.id); setError('');
    try {
      await request(`/api/automation/channels/${channel.id}/oauth/candidates/${candidate.id}`, { method: 'DELETE' });
      window.history.replaceState(null, '', '#channels');
      setCallback({ status: null, code: null, channel: null });
      await onCreated();
    } catch (caught) { setError(caught.message); } finally { setWorking(''); }
  };
  const callbackErrors = {
    authorization_denied: 'Google authorization was cancelled. Nothing was connected.',
    state_expired: 'The connection request expired. Start Connect again.',
    state_invalid: 'The connection response was invalid or already used. Start Connect again.',
    refresh_token_missing: 'Google did not grant offline access. Reconnect and approve the consent screen.',
    required_scope_missing: 'Google did not grant both required YouTube permissions. Reconnect and approve every requested permission.',
    pkce_verifier_missing: 'The secure connection request could not be resumed. Start Connect again.',
    token_exchange_failed: 'Google could not complete authorization. Try Connect again.',
    youtube_channel_missing: 'That Google selection does not have a YouTube channel.',
    youtube_verification_failed: 'YouTube could not verify the selected channel. Check that YouTube Data API v3 is enabled.',
  };
  return <>
    <div className="auto-page-heading"><div><span className="eyebrow">DESTINATIONS</span><h1>Channels</h1><p>Each batch is permanently bound to one destination channel.</p></div><button className="auto-button primary" onClick={() => setAdding(true)}><Plus size={16} /> Register channel</button></div>
    {callback.status === 'success' && <div className="oauth-callout success"><ShieldCheck size={19} /><div><strong>{callback.channel || 'YouTube channel'} connected</strong><span>The exact channel identity was verified. Tokens and resumable sessions are encrypted; eligible private uploads run automatically.</span></div></div>}
    {callback.status === 'pending_adoption' && <div className="oauth-callout"><AlertTriangle size={19} /><div><strong>Google authorized a different YouTube channel</strong><span>Review the discovered channel below. Nothing will change until you explicitly confirm it.</span></div></div>}
    {callback.status === 'error' && <div className="oauth-callout error"><AlertTriangle size={19} /><div><strong>Channel connection was not completed</strong><span>{callbackErrors[callback.code] || 'Start the connection again. No upload was attempted.'}</span></div></div>}
    {!callback.status && <div className={`oauth-callout ${oauthConfigured ? 'success' : ''}`}>{oauthConfigured ? <ShieldCheck size={19} /> : <AlertTriangle size={19} />}<div><strong>{oauthConfigured ? 'Google OAuth is ready' : 'Google OAuth credentials are not configured'}</strong><span>{oauthConfigured ? 'Connect each destination independently. Different YouTube or Brand Account identities are shown for explicit confirmation.' : 'Add a Web application client JSON before trying to connect a destination.'}</span></div></div>}
    {error && <div className="form-error page-form-error"><AlertTriangle size={15} />{error}</div>}
    {!channels.length ? <section className="auto-card"><EmptyState icon={Radio} title="No destination channels" action={<button className="auto-button primary" onClick={() => setAdding(true)}><Plus size={15} /> Register destination</button>}>Add a YouTube channel ID, then authorize that exact channel with Google.</EmptyState></section> :
      <div className="channel-grid">{channels.map((channel) => {
        const connected = channel.oauth_status === 'connected' && channel.google_connection?.status === 'connected';
        const candidate = channel.pending_oauth_candidate;
        const previousMismatch = channel.last_oauth_mismatch;
        return <article className="channel-card" key={channel.id}>
          <div className="channel-card-top">{channel.avatar_url ? <span className="channel-logo image"><img src={channel.avatar_url} alt="" /></span> : <span className="channel-logo">{channel.display_name.slice(0, 2).toUpperCase()}</span>}<div><strong>{channel.google_connection?.channel_title || channel.display_name}</strong><small>{channel.youtube_channel_id}</small></div><StatusPill value={candidate ? 'pending_adoption' : channel.oauth_status} /></div>
          <div className="channel-facts"><span><small>Operational state</small><strong>{prettyState(channel.operational_state)}</strong></span><span><small>Daily limit</small><strong>{channel.uploads_today} / {channel.daily_upload_limit}</strong></span><span><small>Immediate upload cadence</small><strong>{channel.upload_interval_minutes} min</strong></span><span><small>Timezone</small><strong>{channel.timezone}</strong></span></div>
          {connected && <div className="channel-identity"><ShieldCheck size={15} /><span><strong>Verified YouTube identity</strong><small>{channel.google_connection.google_email || 'Google account email unavailable'} · checked {dateTime(channel.google_connection.last_verified_at)}</small></span></div>}
          {candidate && <div className="channel-candidate"><div className="channel-candidate-title"><AlertTriangle size={16} /><span><strong>Google authorized {candidate.channel_title}</strong><small>{candidate.authorized_channel_id}</small></span></div><p>This differs from the local destination ID {channel.youtube_channel_id}. Nothing has been changed or connected yet.</p><div><button className="auto-button primary" disabled={working === channel.id} onClick={() => adoptCandidate(channel, candidate)}><ShieldCheck size={14} /> Use this authorized YouTube channel for this destination</button><button className="auto-button ghost" disabled={working === channel.id} onClick={() => discardCandidate(channel, candidate)}><X size={14} /> Discard</button></div></div>}
          {!candidate && previousMismatch && <div className="channel-candidate historical"><div className="channel-candidate-title"><AlertTriangle size={16} /><span><strong>Google previously authorized {previousMismatch.channel_title || 'a different YouTube channel'}</strong><small>{previousMismatch.authorized_channel_id}</small></span></div><p>That earlier authorization cannot be recovered securely. Reconnect to verify it again and enable explicit confirmation.</p></div>}
          <div className={`channel-footer ${connected ? 'connected' : ''}`}>{connected ? <><button className="auto-button ghost" disabled={working === channel.id} onClick={() => verify(channel)}>{working === channel.id ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} Test connection</button><button className="auto-button danger" disabled={working === channel.id} onClick={() => disconnect(channel)}><Unplug size={14} /> Disconnect Google</button></> : <><span><LockKeyhole size={14} /> {candidate ? 'Confirm or discard the discovered channel' : previousMismatch ? 'Reconnect to confirm this discovered identity' : oauthConfigured ? 'Authorization required before publishing' : 'OAuth client is unavailable'}</span><div className="channel-footer-actions"><button className="auto-button danger" disabled={working === channel.id} onClick={() => removeChannel(channel)}><Trash2 size={14} /> Remove destination</button><button className="auto-button ghost" disabled={!oauthConfigured || working === channel.id} onClick={() => connect(channel)}>{working === channel.id ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {candidate ? 'Choose another channel' : channel.oauth_status === 'not_connected' ? 'Connect with Google' : 'Reconnect'}</button></div></>}</div>
        </article>;
      })}</div>}
    {adding && <div className="auto-modal-backdrop"><form className="auto-modal" onSubmit={submit}><div className="auto-modal-head"><div><span className="eyebrow">LOCAL RECORD</span><h2>Register destination</h2></div><button type="button" onClick={() => setAdding(false)}><X size={18} /></button></div><p className="form-help">This does not connect to Google. Enter the stable YouTube channel ID you intend to authorize later.</p><label><span>Display name</span><input autoFocus required maxLength="120" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Main documentary channel" /></label><label><span>YouTube channel ID</span><input required maxLength="64" value={form.youtube_channel_id} onChange={(event) => setForm({ ...form, youtube_channel_id: event.target.value })} placeholder="UC…" /></label><div className="form-grid"><label><span>Timezone</span><input required value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label><label><span>Daily upload limit</span><input type="number" min="1" max="100" value={form.daily_upload_limit} onChange={(event) => setForm({ ...form, daily_upload_limit: Number(event.target.value) })} /></label><label><span>Minutes between uploads</span><input type="number" min="5" max="1440" value={form.upload_interval_minutes} onChange={(event) => setForm({ ...form, upload_interval_minutes: Number(event.target.value) })} /></label></div>{error && <div className="form-error"><AlertTriangle size={15} />{error}</div>}<div className="auto-modal-actions"><button type="button" className="auto-button ghost" onClick={() => setAdding(false)}>Cancel</button><button className="auto-button primary" disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} Register paused channel</button></div></form></div>}
  </>;
}

function NewBatch({ channels, templates, onCreated, onGoChannels }) {
  const [channelId, setChannelId] = useState('');
  const [name, setName] = useState(`URL batch ${new Date().toLocaleDateString()}`);
  const [urls, setUrls] = useState('');
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [settings, setSettings] = useState(() => ({ template_id: '', output_profile: 'youtube-1080p', metadata_mode: 'preserve', visibility: 'scheduled', scheduled_for: defaultScheduledFor(), schedule_interval_minutes: 30, playlist_ids: [], thumbnail_mode: 'source', delete_source_after_success: true, export_retention_days: 14, notify_subscribers: false }));
  useEffect(() => { if (!settings.template_id && templates[0]) setSettings((current) => ({ ...current, template_id: templates[0].id })); }, [templates, settings.template_id]);
  useEffect(() => {
    if (settings.output_profile === 'youtube-shorts' && settings.thumbnail_mode !== 'none') {
      setSettings((current) => ({ ...current, thumbnail_mode: 'none' }));
    }
  }, [settings.output_profile, settings.thumbnail_mode]);
  useEffect(() => { setPreview(null); }, [channelId, urls]);
  const selectedChannel = channels.find((item) => item.id === channelId);
  const doPreview = async () => {
    setPreviewing(true); setError('');
    try { setPreview(await request('/api/automation/url-batches/preview', { method: 'POST', body: JSON.stringify({ destination_channel_id: channelId, urls }) })); }
    catch (caught) { setError(caught.message); } finally { setPreviewing(false); }
  };
  const create = async () => {
    setCreating(true); setError('');
    try {
      const batch = await request('/api/automation/url-batches', { method: 'POST', body: JSON.stringify({ destination_channel_id: channelId, name, urls, settings }) });
      await onCreated(batch);
    } catch (caught) { setError(caught.message); } finally { setCreating(false); }
  };
  if (!channels.length) return <><div className="auto-page-heading"><div><span className="eyebrow">DESTINATION FIRST</span><h1>New URL Batch</h1><p>Choose where edited videos will go before adding links.</p></div></div><section className="auto-card"><EmptyState icon={Radio} title="Register a destination first" action={<button className="auto-button primary" onClick={onGoChannels}><Plus size={15} /> Register channel</button>}>A batch cannot exist without an explicit destination channel.</EmptyState></section></>;
  return <>
    <div className="auto-page-heading"><div><span className="eyebrow">DESTINATION FIRST</span><h1>New URL Batch</h1><p>Validate links and freeze every setting into an immutable batch.</p></div></div>
    <div className="wizard-steps"><span className={channelId ? 'done' : 'active'}><i>{channelId ? <Check size={13} /> : '1'}</i>Destination</span><b /><span className={preview ? 'done' : channelId ? 'active' : ''}><i>{preview ? <Check size={13} /> : '2'}</i>Links</span><b /><span className={preview?.counts?.accepted ? 'active' : ''}><i>3</i>Settings & confirm</span></div>
    <section className="auto-card batch-builder">
      <div className="builder-section"><div className="builder-number">1</div><div className="builder-content"><div className="builder-title"><div><h2>Choose destination channel</h2><p>This channel is locked to the batch after creation.</p></div></div><div className="destination-options">{channels.map((channel) => <button key={channel.id} className={channelId === channel.id ? 'selected' : ''} onClick={() => setChannelId(channel.id)}><span className="channel-logo small">{channel.display_name.slice(0, 2).toUpperCase()}</span><span><strong>{channel.display_name}</strong><small>{channel.youtube_channel_id}</small></span><StatusPill value={channel.oauth_status} /><i className="selection-ring">{channelId === channel.id && <Check size={13} />}</i></button>)}</div></div></div>
      <div className={`builder-section ${!channelId ? 'disabled' : ''}`}><div className="builder-number">2</div><div className="builder-content"><div className="builder-title"><div><h2>Add YouTube links</h2><p>Paste URLs separated by new lines, spaces, or commas.</p></div></div><textarea disabled={!channelId} value={urls} onChange={(event) => setUrls(event.target.value)} rows="7" placeholder={'https://www.youtube.com/watch?v=…\nhttps://youtu.be/…'} /><div className="builder-actions"><span>{urls.trim() ? 'Ready to normalize and check duplicates' : 'No links entered'}</span><button className="auto-button dark" disabled={!channelId || !urls.trim() || previewing} onClick={doPreview}>{previewing ? <LoaderCircle className="spin" size={16} /> : <ListChecks size={16} />} Validate links</button></div>{preview && <div className="url-preview"><div className="preview-summary"><span className="accepted"><Check size={14} />{preview.counts.accepted} accepted</span><span className="duplicate"><FileClock size={14} />{preview.counts.duplicate} duplicate</span><span className="invalid"><AlertTriangle size={14} />{preview.counts.invalid} invalid</span></div><div className="preview-table">{preview.rows.map((row) => <div key={`${row.position}-${row.raw_url}`}><span className="video-thumb">{row.thumbnail_url ? <img src={row.thumbnail_url} alt="" /> : <Video size={18} />}</span><span><strong>{row.source_video_id || row.raw_url}</strong><small>{row.normalized_url || row.raw_url}</small></span><StatusPill value={row.status} /><small className="row-message">{row.message}</small></div>)}</div></div>}</div></div>
      <div className={`builder-section ${!preview?.counts?.accepted ? 'disabled' : ''}`}><div className="builder-number">3</div><div className="builder-content"><div className="builder-title"><div><h2>Freeze pipeline settings</h2><p>These values are copied into an immutable version for every accepted item.</p></div></div><div className="batch-settings"><label><span>Batch name</span><input disabled={!preview?.counts?.accepted} value={name} maxLength="140" onChange={(event) => setName(event.target.value)} /></label><label><span>Remix preset</span><select disabled={!preview?.counts?.accepted} value={settings.template_id} onChange={(event) => setSettings({ ...settings, template_id: event.target.value })}>{templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label><label><span>Output format</span><select value={settings.output_profile} onChange={(event) => setSettings({ ...settings, output_profile: event.target.value })}><option value="youtube-1080p">YouTube Landscape · 1080p</option><option value="youtube-1440p">YouTube Landscape · 1440p</option><option value="youtube-shorts">YouTube Shorts · 1080×1920</option><option value="preserve-source">Preserve source</option></select></label><label><span>Metadata behavior</span><select value={settings.metadata_mode} onChange={(event) => setSettings({ ...settings, metadata_mode: event.target.value })}><option value="preserve">Preserve source metadata</option><option value="template">Apply metadata template</option><option value="rewrite">Rewrite later</option><option value="manual">Manual review</option></select></label><label><span>Visibility</span><select value={settings.visibility} onChange={(event) => setSettings({ ...settings, visibility: event.target.value, scheduled_for: event.target.value === 'scheduled' ? (settings.scheduled_for || defaultScheduledFor()) : null })}><option value="scheduled">Scheduled public</option><option value="public">Public immediately</option></select></label><label><span>First public release {settings.visibility === 'scheduled' ? '(required)' : '(unused)'}</span><input type="datetime-local" disabled={settings.visibility !== 'scheduled'} value={dateTimeInputValue(settings.scheduled_for)} onChange={(event) => setSettings({ ...settings, scheduled_for: event.target.value ? new Date(event.target.value).toISOString() : null })} /></label><label><span>Minutes between public releases</span><input type="number" min="5" max="1440" disabled={settings.visibility !== 'scheduled'} value={settings.schedule_interval_minutes} onChange={(event) => setSettings({ ...settings, schedule_interval_minutes: Number(event.target.value) })} /></label><label><span>Playlist IDs (comma separated)</span><input value={settings.playlist_ids.join(', ')} onChange={(event) => setSettings({ ...settings, playlist_ids: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} placeholder="PL… , PL…" /></label><label><span>Thumbnail</span><select value={settings.thumbnail_mode} onChange={(event) => setSettings({ ...settings, thumbnail_mode: event.target.value })}><option value="source">Use source thumbnail</option><option value="generated">Generate from render</option><option value="custom">Choose custom later</option><option value="none">No custom thumbnail</option></select></label><label><span>Export retention</span><select value={settings.export_retention_days} onChange={(event) => setSettings({ ...settings, export_retention_days: Number(event.target.value) })}><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="90">90 days</option></select></label><label className="toggle-setting"><span><strong>Notify subscribers</strong><small>Applied when YouTube starts the upload</small></span><input type="checkbox" checked={settings.notify_subscribers} onChange={(event) => setSettings({ ...settings, notify_subscribers: event.target.checked })} /></label><label className="toggle-setting"><span><strong>Delete source after success</strong><small>Only after YouTube confirms processing</small></span><input type="checkbox" checked={settings.delete_source_after_success} onChange={(event) => setSettings({ ...settings, delete_source_after_success: event.target.checked })} /></label></div>{settings.visibility === 'scheduled' && <div className="schedule-note"><CalendarClock size={16} /><span>YouTube requires scheduled uploads to remain private until release, then makes them public automatically, {settings.schedule_interval_minutes} minutes apart.</span></div>}{selectedChannel && <div className="confirmation-strip"><span className="channel-logo small">{selectedChannel.display_name.slice(0, 2).toUpperCase()}</span><span><strong>{preview?.counts?.accepted || 0} videos → {selectedChannel.display_name}</strong><small>{settings.visibility === 'scheduled' ? 'scheduled public' : 'public immediately'}{settings.scheduled_for ? ` · first ${dateTime(settings.scheduled_for)} · every ${settings.schedule_interval_minutes} min` : ''} · {settings.playlist_ids.length} playlist{settings.playlist_ids.length === 1 ? '' : 's'} · {settings.export_retention_days}-day export retention · source cleanup {settings.delete_source_after_success ? 'enabled' : 'disabled'}</small></span><button className="auto-button primary" disabled={creating || !name.trim() || !settings.template_id || (settings.visibility === 'scheduled' && !settings.scheduled_for)} onClick={create}>{creating ? <LoaderCircle className="spin" size={16} /> : <UploadCloud size={16} />} Create immutable batch</button></div>}{error && <div className="form-error"><AlertTriangle size={15} />{error}</div>}</div></div>
    </section>
  </>;
}

function SourceFeeds({ channels, templates, feeds, onChanged, onGoChannels }) {
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [form, setForm] = useState({
    destination_channel_id: '',
    name: `Channel import ${new Date().toLocaleDateString()}`,
    source_channel_url: '',
    settings: { template_id: '', output_profile: 'youtube-1080p', metadata_mode: 'preserve', visibility: 'scheduled', scheduled_for: defaultScheduledFor(), schedule_interval_minutes: 30, playlist_ids: [], thumbnail_mode: 'source', delete_source_after_success: true, export_retention_days: 14, notify_subscribers: false },
  });
  useEffect(() => {
    if (!form.settings.template_id && templates[0]) setForm((current) => ({ ...current, settings: { ...current.settings, template_id: templates[0].id } }));
  }, [templates, form.settings.template_id]);
  useEffect(() => {
    if (form.settings.output_profile === 'youtube-shorts' && form.settings.thumbnail_mode !== 'none') {
      setForm((current) => ({ ...current, settings: { ...current.settings, thumbnail_mode: 'none' } }));
    }
  }, [form.settings.output_profile, form.settings.thumbnail_mode]);
  const updateSetting = (key, value) => setForm((current) => ({ ...current, settings: { ...current.settings, [key]: value } }));
  const submit = async (event) => {
    event.preventDefault(); setSaving(true); setError(''); setResult(null);
    try {
      const created = await request('/api/automation/source-feeds', { method: 'POST', body: JSON.stringify(form) });
      setResult(created);
      setForm((current) => ({ ...current, name: `Channel import ${new Date().toLocaleDateString()}`, source_channel_url: '' }));
      await onChanged();
    } catch (caught) { setError(caught.message); } finally { setSaving(false); }
  };
  const sync = async (feed) => {
    setSyncing(feed.id); setError(''); setResult(null);
    try { const synced = await request(`/api/automation/source-feeds/${feed.id}/sync`, { method: 'POST' }); setResult(synced); await onChanged(); }
    catch (caught) { setError(caught.message); } finally { setSyncing(''); }
  };
  if (!channels.length) return <><div className="auto-page-heading"><div><span className="eyebrow">AUTOMATED INTAKE</span><h1>Source channels</h1><p>Import a channel only after choosing its destination.</p></div></div><section className="auto-card"><EmptyState icon={Radio} title="Register a destination first" action={<button className="auto-button primary" onClick={onGoChannels}><Plus size={15} /> Register channel</button>}>Every discovered source video must remain bound to one explicit destination.</EmptyState></section></>;
  return <>
    <div className="auto-page-heading"><div><span className="eyebrow">AUTOMATED INTAKE</span><h1>Source channels</h1><p>Add a YouTube channel or @handle. Every public video is discovered and queued for alteration.</p></div></div>
    {result && <div className="oauth-callout success"><Check size={18} /><div><strong>{result.feed.source_channel_title || result.feed.name} synchronized</strong><span>{result.feed.discovered_count} discovered · {result.feed.queued_count} newly queued · {result.feed.duplicate_count} already recorded. Completed renders upload independently and YouTube releases scheduled videos {result.feed.settings_snapshot.schedule_interval_minutes || 30} minutes apart.</span></div></div>}
    {error && <div className="form-error page-form-error"><AlertTriangle size={15} />{error}</div>}
    <section className="auto-card source-feed-builder">
      <form onSubmit={submit}>
        <div className="builder-title"><div><h2>Import an entire YouTube channel</h2><p>Discovery reads channel metadata only. Media downloads continue one by one through the durable worker.</p></div></div>
        <div className="source-feed-form">
          <label><span>Destination channel</span><select required value={form.destination_channel_id} onChange={(event) => setForm({ ...form, destination_channel_id: event.target.value })}><option value="">Choose destination…</option>{channels.map((channel) => <option key={channel.id} value={channel.id}>{channel.display_name} · scheduled uploads start immediately</option>)}</select></label>
          <label><span>Pipeline name</span><input required maxLength="140" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label className="source-url-field"><span>YouTube source channel URL</span><input required maxLength="500" value={form.source_channel_url} onChange={(event) => setForm({ ...form, source_channel_url: event.target.value })} placeholder="https://www.youtube.com/@channel" /></label>
          <label><span>Remix preset</span><select required value={form.settings.template_id} onChange={(event) => updateSetting('template_id', event.target.value)}>{templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
          <label><span>Output format</span><select value={form.settings.output_profile} onChange={(event) => updateSetting('output_profile', event.target.value)}><option value="youtube-1080p">YouTube Landscape · 1080p</option><option value="youtube-1440p">YouTube Landscape · 1440p</option><option value="youtube-shorts">YouTube Shorts · 1080×1920</option><option value="preserve-source">Preserve source</option></select></label>
          <label><span>Visibility</span><select value={form.settings.visibility} onChange={(event) => { updateSetting('visibility', event.target.value); if (event.target.value === 'scheduled' && !form.settings.scheduled_for) updateSetting('scheduled_for', defaultScheduledFor()); }}><option value="scheduled">Scheduled public</option><option value="public">Public immediately</option></select></label>
          <label><span>First public release</span><input type="datetime-local" disabled={form.settings.visibility !== 'scheduled'} value={dateTimeInputValue(form.settings.scheduled_for)} onChange={(event) => updateSetting('scheduled_for', event.target.value ? new Date(event.target.value).toISOString() : null)} /></label>
          <label><span>Minutes between public releases</span><input type="number" min="5" max="1440" disabled={form.settings.visibility !== 'scheduled'} value={form.settings.schedule_interval_minutes} onChange={(event) => updateSetting('schedule_interval_minutes', Number(event.target.value))} /></label>
          <label className="toggle-setting"><span><strong>Delete source after success</strong><small>Only after YouTube confirms processing</small></span><input type="checkbox" checked={form.settings.delete_source_after_success} onChange={(event) => updateSetting('delete_source_after_success', event.target.checked)} /></label>
        </div>
        <div className="source-feed-submit"><span><Clock3 size={14} /> Each completed render uploads independently; YouTube publishes scheduled videos even when this system is off.</span><button className="auto-button primary" disabled={saving || !form.destination_channel_id || !form.source_channel_url || !form.settings.template_id || (form.settings.visibility === 'scheduled' && !form.settings.scheduled_for)}>{saving ? <LoaderCircle className="spin" size={16} /> : <Rss size={16} />} {saving ? 'Discovering channel…' : 'Import all videos'}</button></div>
      </form>
    </section>
    <section className="auto-card source-feed-list"><div className="auto-card-head"><div><span className="eyebrow">CONFIGURED SOURCES</span><h2>Channel imports</h2></div></div>{!feeds.length ? <EmptyState icon={Rss} title="No source channels yet">Add a source channel above to create its first durable import batch.</EmptyState> : feeds.map((feed) => <article key={feed.id}><span className="source-feed-icon"><Rss size={17} /></span><span><strong>{feed.source_channel_title || feed.name}</strong><small>{feed.source_channel_url}</small></span><span><small>Destination</small><strong>{feed.destination_channel.display_name}</strong></span><span><small>Last sync</small><strong>{dateTime(feed.last_synced_at)}</strong></span><span><small>Last result</small><strong>{feed.queued_count} queued · {feed.duplicate_count} skipped</strong></span><StatusPill value={feed.state} /><button className="auto-button ghost" disabled={syncing === feed.id} onClick={() => sync(feed)}>{syncing === feed.id ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} Sync new videos</button></article>)}</section>
  </>;
}

function Queue({ batches, onRefresh }) {
  const [retrying, setRetrying] = useState('');
  const [controlling, setControlling] = useState('');
  const [scheduling, setScheduling] = useState('');
  const [error, setError] = useState('');
  const retry = async (kind, jobId) => {
    setRetrying(jobId);
    try { await request(`/api/automation/${kind}-jobs/${jobId}/retry`, { method: 'POST' }); await onRefresh(); }
    finally { setRetrying(''); }
  };
  const setProcessing = async (batch, action) => {
    setControlling(batch.id); setError('');
    try { await request(`/api/automation/url-batches/${batch.id}/${action}-processing`, { method: 'POST' }); await onRefresh(); }
    catch (caught) { setError(caught.message); } finally { setControlling(''); }
  };
  const scheduleRemaining = async (batch) => {
    setScheduling(batch.id); setError('');
    try {
      await request(`/api/automation/url-batches/${batch.id}/schedule-remaining`, {
        method: 'POST',
        body: JSON.stringify({ schedule_interval_minutes: 30 }),
      });
      await onRefresh();
    } catch (caught) { setError(caught.message); } finally { setScheduling(''); }
  };
  return <>
    <div className="auto-page-heading"><div><span className="eyebrow">DURABLE STATE</span><h1>Work queue</h1><p>Accepted links download and render automatically. Progress survives API restarts.</p></div><button className="auto-button ghost" onClick={onRefresh}><RefreshCw size={16} /> Refresh</button></div>
    {error && <div className="form-error page-form-error"><AlertTriangle size={15} />{error}</div>}
    {!batches.length ? <section className="auto-card"><EmptyState icon={ListChecks} title="Queue is empty">Create a URL batch to download sources and produce altered exports.</EmptyState></section> : <div className="queue-batches">{batches.map((batch) => {
      const accepted = batch.items.filter((item) => item.render_job);
      const batchProgress = accepted.length ? Math.round(accepted.reduce((sum, item) => sum + ((item.render_job?.progress || 0) + (item.publish_job?.progress || 0)) / 2, 0) / accepted.length) : 0;
      const canScheduleRemaining = batch.settings_snapshot.visibility !== 'scheduled' && batch.items.some((item) => item.publish_job && !item.publish_job.youtube_video_id && ['waiting_for_render', 'waiting_for_upload', 'blocked_not_connected', 'upload_failed'].includes(item.publish_job.state));
      return <section className="auto-card queue-batch" key={batch.id}>
        <div className="queue-batch-head"><div><span className="batch-avatar">{batch.destination_channel.display_name.slice(0, 2).toUpperCase()}</span><span><strong>{batch.name}</strong><small>{batch.destination_channel.display_name} · {dateTime(batch.created_at)}</small></span></div><div className="batch-status"><StatusPill value={batch.state} /><StatusPill value={batch.processing_state} />{canScheduleRemaining && <button className="batch-processing-control" disabled={scheduling === batch.id} onClick={() => scheduleRemaining(batch)} title="Upload all remaining videos privately with 30-minute public release slots">{scheduling === batch.id ? <LoaderCircle className="spin" size={14} /> : <CalendarClock size={14} />} Schedule remaining</button>}<button className="batch-processing-control" disabled={controlling === batch.id} onClick={() => setProcessing(batch, batch.processing_state === 'paused' ? 'resume' : 'pause')} title={batch.processing_state === 'paused' ? 'Continue processing the remaining videos' : 'Finish the current alteration, then stop claiming videos'}>{controlling === batch.id ? <LoaderCircle className="spin" size={14} /> : batch.processing_state === 'paused' ? <Play size={14} /> : <Pause size={14} />} {batch.processing_state === 'paused' ? 'Resume processing' : 'Pause processing'}</button><strong>{batchProgress}%</strong></div></div>
        <div className="batch-progress" aria-label={`Batch ${batchProgress}% complete`}><i style={{ width: `${batchProgress}%` }} /></div>
        <div className="queue-summary"><span>{batch.accepted_count} accepted</span><span>{batch.duplicate_count} duplicates</span><span>{batch.invalid_count} invalid</span><span>Pipeline {batch.pipeline_version_id.slice(0, 8)}</span>{batch.settings_snapshot.visibility === 'scheduled' && <span>Public releases every {batch.settings_snapshot.schedule_interval_minutes || 30} min from {dateTime(batch.settings_snapshot.scheduled_for)}</span>}{batch.processing_state === 'paused' && <span className="pause-explanation">New alterations paused; completed videos keep uploading</span>}</div>
        <div className="queue-items">{batch.items.map((item) => {
          const renderProgress = item.render_job?.state === 'completed' ? 100 : (item.render_job?.progress || 0);
          const publishProgress = ['published', 'scheduled'].includes(item.publish_job?.state) ? 100 : (item.publish_job?.progress || 0);
          const canEdit = Boolean(item.source_asset?.state === 'ready' && item.source_asset?.editor_video_id && item.render_job && !['acquiring', 'downloading', 'rendering', 'validating', 'edit_requested'].includes(item.render_job.state));
          const publishFailed = ['upload_failed', 'processing_failed', 'post_publish_failed'].includes(item.publish_job?.state);
          const editHref = canEdit ? `#editor?${new URLSearchParams({ render_job_id: item.render_job.id, editor_video_id: item.source_asset.editor_video_id, template_id: batch.settings_snapshot.template_id, title: item.source_title || item.source_video_id || 'Automation video' })}` : '';
          return <div className="queue-item" key={item.id}>
            <span className="video-thumb">{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : <Video size={18} />}</span>
            <span className="queue-source"><strong>{item.source_title || item.source_video_id || 'Invalid URL'}</strong><small>{item.normalized_url || item.raw_url}</small></span>
            <span className="queue-stage"><small>Item</small><StatusPill value={item.state} /><b>{renderProgress}%</b></span>
            <span className="render-state"><small>Altered export</small>{item.render_job ? <><span className="state-with-percent"><StatusPill value={item.render_job.state} /><b>{renderProgress}%</b></span><span className="render-progress"><i style={{ width: `${renderProgress}%` }} /></span>{item.render_job.error_message && <em title={item.render_job.error_message}>{item.render_job.error_message}</em>}{['download_failed', 'render_failed'].includes(item.render_job.state) && <button disabled={retrying === item.render_job.id} onClick={() => retry('render', item.render_job.id)}><RotateCcw size={12} /> Retry</button>}{item.output && <a href={`${API}${item.output.url}`}><Download size={12} /> Download altered video</a>}</> : '—'}</span>
            <span className="queue-stage"><small>Publish</small>{item.publish_job ? <><StatusPill value={item.publish_job.state} /><b>{publishProgress}%</b>{item.publish_job.visibility === 'scheduled' && item.publish_job.scheduled_for && <small className="publication-time" title={`YouTube public release: ${publicationDateTime(item.publish_job.scheduled_for)}`}><CalendarClock size={12} /> Live on YouTube {publicationDateTime(item.publish_job.scheduled_for)}</small>}{item.publish_job.error_message && <em title={item.publish_job.error_message}>{item.publish_job.error_message}</em>}</> : '—'}</span>
            <span className="queue-actions"><small>Actions</small>{canEdit ? <a href={editHref} title="Open this source in the advanced editor"><Pencil size={13} /> Edit video</a> : <button disabled title="The retained source is not currently editable"><Pencil size={13} /> Edit video</button>}{publishFailed && <button className="publish-action" disabled={retrying === item.publish_job.id} onClick={() => retry('publish', item.publish_job.id)}><RotateCcw size={13} /> Retry upload</button>}{item.publish_job?.youtube_video_id && <a href={`https://youtu.be/${item.publish_job.youtube_video_id}`} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Open on YouTube</a>}</span>
          </div>;
        })}</div>
      </section>;
    })}</div>}
  </>;
}

function EventHistory({ events }) {
  return <><div className="auto-page-heading"><div><span className="eyebrow">APPEND-ONLY AUDIT</span><h1>History</h1><p>Control-plane events remain after media cleanup.</p></div></div><section className="auto-card">{!events.length ? <EmptyState icon={History} title="No events yet">Channel and batch activity will appear here.</EmptyState> : <div className="event-list">{events.map((event) => <div key={event.id}><span className="event-dot"><Activity size={14} /></span><span><strong>{prettyState(event.event_type)}</strong><small>{event.aggregate_type} · {event.aggregate_id.slice(0, 8)}</small></span><StatusPill value={event.to_state || 'recorded'} /><time>{dateTime(event.occurred_at)}</time></div>)}</div>}</section></>;
}

export default function AutomationDashboard({ onOpenEditor, initialSection = 'overview' }) {
  const [section, setSection] = useState(initialSection);
  const [overview, setOverview] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [sourceFeeds, setSourceFeeds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mobileNav, setMobileNav] = useState(false);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [data, presetData, feedData] = await Promise.all([request('/api/automation/overview'), request('/api/templates'), request('/api/automation/source-feeds')]);
      setOverview(data); setTemplates(presetData.filter((item) => !item.invalid));
      setSourceFeeds(feedData);
    } catch (caught) { setError(caught.message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (section !== 'queue') return undefined;
    const timer = window.setInterval(async () => {
      try { setOverview(await request('/api/automation/overview')); } catch { /* manual refresh reports errors */ }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [section]);
  const navigate = (target) => {
    window.history.replaceState(null, '', target === 'overview' ? '#' : `#${target}`);
    setSection(target);
    setMobileNav(false);
  };
  const page = useMemo(() => {
    if (section === 'channels') return <Channels channels={overview?.channels || []} oauthConfigured={overview?.oauth_configured} onCreated={load} />;
    if (section === 'new-batch') return <NewBatch channels={overview?.channels || []} templates={templates} onGoChannels={() => navigate('channels')} onCreated={async () => { await load(); navigate('queue'); }} />;
    if (section === 'source-feeds') return <SourceFeeds channels={overview?.channels || []} templates={templates} feeds={sourceFeeds} onGoChannels={() => navigate('channels')} onChanged={load} />;
    if (section === 'queue') return <Queue batches={overview?.recent_batches || []} onRefresh={load} />;
    if (section === 'history') return <EventHistory events={overview?.recent_events || []} />;
    return <Overview data={overview} loading={loading} onNavigate={navigate} onRefresh={load} />;
  }, [section, overview, templates, sourceFeeds, loading, load]);
  return <div className="automation-shell">
    <aside className={mobileNav ? 'open' : ''}><div className="auto-brand"><span><Clapperboard size={20} /></span><div><strong>Remix Studio</strong><small>Automation console</small></div><button onClick={() => setMobileNav(false)}><X size={18} /></button></div><nav>{navItems.map(([id, Icon, label]) => <button key={id} className={section === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={18} />{label}{id === 'queue' && !!overview?.counts?.items_by_state?.queued && <b>{overview.counts.items_by_state.queued}</b>}</button>)}</nav><div className="auto-sidebar-bottom"><button onClick={onOpenEditor}><Settings2 size={18} /><span><strong>Advanced editor</strong><small>Templates & renders</small></span><ExternalLink size={14} /></button><div><span className="owner-avatar">AD</span><span><strong>Administrator</strong><small>Local owner session</small></span><CircleDot size={13} /></div></div></aside>
    {mobileNav && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <main><header className="auto-topbar"><button className="mobile-menu" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div><span className="system-live"><i /> PostgreSQL online</span><span className="top-divider" /><span className="development-mode"><LockKeyhole size={13} /> Safe development mode</span></div><button className="new-batch-top" onClick={() => navigate('new-batch')}><Plus size={15} /> New batch</button></header><div className="auto-content">{error && <div className="page-error"><AlertTriangle size={17} /><span><strong>Could not load automation data</strong>{error}</span><button onClick={load}>Retry</button></div>}{loading && !overview ? <div className="auto-loading"><LoaderCircle className="spin" /><span>Loading durable control plane…</span></div> : page}</div></main>
  </div>;
}
