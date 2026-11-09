// UploadPage (photographer or organiser): upload event photos into quarantine for approval.
// Requires a passkey session.
import { useState, type FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/apiClient';

function Upload({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<FileList | null>(null);
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle');
  const [detail, setDetail] = useState('');

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!files || files.length === 0) return;
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    setState('sending');
    try {
      await api(`/events/${eventId}/photos`, { body: form, timeoutMs: 300_000 });
      setState('done');
    } catch (error) {
      setDetail(error instanceof Error ? error.message : '');
      setState('error');
    }
  }

  return (
    <form className="card" onSubmit={(e) => void send(e)}>
      <label htmlFor="photos">{t('upload.choose')}</label>
      <input
        id="photos"
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp"
        onChange={(e) => setFiles(e.target.files)}
      />
      <button type="submit" disabled={state === 'sending'}>
        {state === 'sending' ? t('upload.sending') : t('upload.send')}
      </button>
      {state === 'done' && <p role="status">{t('upload.done')}</p>}
      {state === 'error' && (
        <p role="alert">
          {t('common.error')} {detail}
        </p>
      )}
    </form>
  );
}

export default function UploadPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  return (
    <>
      <h1>{t('upload.title')}</h1>
      <PasskeyGate next={`/photographer/events/${eventId}/upload`}>
        <Upload eventId={eventId} />
      </PasskeyGate>
    </>
  );
}
