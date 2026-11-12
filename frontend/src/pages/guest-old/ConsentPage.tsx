// ConsentPage: the age question, then the versioned notice (English/Hindi, draft text) and a
// separate consent for face search in this event.
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useGuestStatus } from '@/features/consent/useGuestStatus';
import { api } from '@/lib/apiClient';

interface Notice {
  version: string;
  draft: boolean;
  texts: Record<string, Record<string, string>>;
}

export default function ConsentPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { eventId = '' } = useParams();
  const { status, error } = useGuestStatus(eventId);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState('');
  const lang = i18n.language === 'hi' ? 'hi' : 'en';

  useEffect(() => {
    api<Notice>('/consent/notice')
      .then(setNotice)
      .catch(() => setMessage(t('common.error')));
  }, [t]);

  useEffect(() => {
    if (status?.consent_current) navigate(`/events/${eventId}/selfie`, { replace: true });
  }, [status, eventId, navigate]);

  if (error) return <p role="alert">{t('common.error')}</p>;
  if (!status || !notice) return <p>{t('common.loading')}</p>;
  const text = notice.texts[lang] ?? notice.texts.en ?? {};

  async function consent() {
    try {
      await api(`/events/${eventId}/consent`, {
        json: { notice_version: notice?.version, language: lang, face_search: true },
      });
      navigate(`/events/${eventId}/selfie`);
    } catch {
      setMessage(t('consent.outdated'));
    }
  }

  return (
    <section className="card">
      <h1>{text.title}</h1>
      {notice.draft && <p className="banner">{text.draft_banner}</p>}
      <p className="notice">{text.notice}</p>
      <label className="check">
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        <span>{text.consent_label}</span>
      </label>
      <button type="button" disabled={!agreed} onClick={() => void consent()}>
        {t('consent.submit')}
      </button>
      {message && <p role="alert">{message}</p>}
    </section>
  );
}
