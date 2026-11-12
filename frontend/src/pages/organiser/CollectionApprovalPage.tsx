// CollectionApprovalPage (organiser): review upload batches (signed previews) and approve them;
// the worker then indexes the event. Requires a passkey session.
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PasskeyGate } from '@/features/auth/PasskeyGate';
import { api } from '@/lib/serverApi';

interface Collection {
  collection_id: string;
  status: string;
  photos: number;
  previews: string[];
}

interface EventDetail {
  name: string;
  status: string;
  counts: Record<string, number> | null;
}

function Approval({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    api<{ collections: Collection[] }>(`/events/${eventId}/collections`)
      .then((data) => setCollections(data.collections))
      .catch(() => setCollections([]));
    api<EventDetail>(`/events/${eventId}`)
      .then(setEvent)
      .catch(() => setEvent(null));
  }, [eventId]);

  useEffect(load, [load]);
  useEffect(() => {
    if (event?.status !== 'indexing') return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [event?.status, load]);

  async function approve(collectionId: string) {
    try {
      await api(`/events/${eventId}/collections/${collectionId}/approve`, { method: 'POST' });
      setMessage(t('approval.indexing'));
      load();
    } catch {
      setMessage(t('common.error'));
    }
  }

  return (
    <>
      {event && (
        <p>
          <strong>{event.name}</strong>: {t('organiser.status')} {event.status}
          {event.counts && ` (${event.counts.photos} photos, ${event.counts.faces} faces)`}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {collections.map((c) => (
        <section className="card" key={c.collection_id}>
          <h2>
            {t('approval.collection')}: {c.photos} {t('approval.photos')}
          </h2>
          <ul className="gallery small">
            {c.previews.map((url) => (
              <li key={url}>
                <img src={url} alt="" loading="lazy" />
              </li>
            ))}
          </ul>
          {c.status === 'open' ? (
            <button type="button" onClick={() => void approve(c.collection_id)}>
              {t('approval.approve')}
            </button>
          ) : (
            <p>{t('approval.approved')}</p>
          )}
        </section>
      ))}
    </>
  );
}

export default function CollectionApprovalPage() {
  const { t } = useTranslation();
  const { eventId = '' } = useParams();
  return (
    <>
      <h1>{t('approval.title')}</h1>
      <PasskeyGate next={`/organiser/events/${eventId}/approval`}>
        <Approval eventId={eventId} />
      </PasskeyGate>
    </>
  );
}
