// UploadPage (photographer or organiser): upload event photos into quarantine for approval.
// Requires a passkey session.
import { useState, type FormEvent } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/apiClient';

interface UploadResult {
  collection_id: string;
  photo_ids?: string[];
  already_uploaded?: number[];
  message?: string;
}

function Upload({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<FileList | null>(null);
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle');
  const [progress, setProgress] = useState<{ current: number; total: number; name: string } | null>(
    null,
  );
  const [detail, setDetail] = useState('');
  const [duplicates, setDuplicates] = useState<string[]>([]);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!files || files.length === 0) return;
    const fileList = Array.from(files);
    setState('sending');
    setDetail('');
    setDuplicates([]);

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
        const resp = await api<UploadResult>(`/events/${eventId}/photos`, {
          body: form,
          timeoutMs: 120_000,
        });
        if (!collectionId && resp.collection_id) {
          collectionId = resp.collection_id;
        }
        if (
          resp.message === 'already uploaded' ||
          (resp.already_uploaded && resp.already_uploaded.length > 0)
        ) {
          setDuplicates((prev) => [...prev, file.name]);
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
      <div
        className="row"
        style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}
      >
        <h2>{t('organiser.upload')}</h2>
        <Link to={`/photographer/events/${eventId}/status`} className="link">
          Processing Status
        </Link>
      </div>
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
      {duplicates.length > 0 && (
        <p role="status" className="banner">
          {t('upload.alreadyUploaded', { names: duplicates.join(', ') })}
        </p>
      )}
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
