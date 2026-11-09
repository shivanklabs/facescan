// PrivacySettingsPage: the user's face-search consents per event, withdrawal, and signing out
// on all devices.
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/apiClient';

interface ConsentRecord {
  event_id: string;
  event_name: string;
  purpose: string;
  granted_at: string;
  withdrawn_at: string | null;
}

export default function PrivacySettingsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [records, setRecords] = useState<ConsentRecord[] | null>(null);
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    api<{ consents: ConsentRecord[] }>('/me/consents')
      .then((data) => setRecords(data.consents.filter((c) => c.purpose === 'face_search')))
      .catch(() => setRecords([]));
  }, []);

  useEffect(load, [load]);

  async function withdraw(eventId: string) {
    if (!window.confirm(t('privacy.confirm'))) return;
    try {
      await api(`/events/${eventId}/consent/withdraw`, { method: 'POST' });
      setMessage(t('privacy.done'));
      load();
    } catch {
      setMessage(t('common.error'));
    }
  }

  async function signOutEverywhere() {
    await api('/auth/logout-everywhere', { method: 'POST' }).catch(() => undefined);
    navigate('/');
  }

  return (
    <>
      <h1>{t('privacy.title')}</h1>
      {message && <p role="status">{message}</p>}
      {records === null && <p>{t('common.loading')}</p>}
      {records?.length === 0 && <p>{t('privacy.none')}</p>}
      <ul className="list">
        {records?.map((record) => (
          <li key={`${record.event_id}-${record.granted_at}`}>
            <strong>{record.event_name}</strong>
            <div>
              {record.withdrawn_at
                ? `${t('privacy.withdrawnOn')}: ${new Date(record.withdrawn_at).toLocaleString()}`
                : t('privacy.active')}
            </div>
            {!record.withdrawn_at && (
              <button
                type="button"
                className="danger"
                onClick={() => void withdraw(record.event_id)}
              >
                {t('privacy.withdraw')}
              </button>
            )}
          </li>
        ))}
      </ul>
      <button type="button" className="secondary" onClick={() => void signOutEverywhere()}>
        {t('common.signOutEverywhere')}
      </button>
    </>
  );
}
