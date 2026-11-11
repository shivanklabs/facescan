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
  const [progress, setProgress] = useState<{ current: number; total: number; name: string } | null>(
    null
  );
  const [detail, setDetail] = useState('');

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!files || files.length === 0) return;
    const fileList = Array.from(files);
    setState('sending');
    setDetail('');

    let collectionId: string | null = null;

    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];
      if (!file) continue;
      setProgress({ current: i + 1, total: fileList.length, name: file.name });

      const form = new FormData();
      form.append('files', file);
      if (collectionId) {
        form.append('collection_id', collectionId);
      }

      try {
        const resp = await api<{ collection_id: string }>(`/events/${eventId}/photos`, {
          body: form,
          timeoutMs: 120_000,
        });
        if (!collectionId && resp.collection_id) {
          collectionId = resp.collection_id;
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : 'Upload failed';
        setDetail(`Failed uploading "${file.name}": ${msg}. Please retry.`);
        setState('error');
        setProgress(null);
        return;
      }
    }

    setProgress(null);
    setState('done');
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
        {state === 'sending'
          ? progress
            ? `Uploading ${progress.current}/${progress.total} (${progress.name})…`
            : t('upload.sending')
          : t('upload.send')}
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
