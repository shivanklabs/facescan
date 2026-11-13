// HelpChat: the guest help assistant (owner decision 13 Nov 2026: a fixed help bot, no AI model).
// A bubble in the bottom-right corner opens a small chat with ready answers in English and Hindi
// and a button to the right page. Nothing typed here leaves the browser: answers are picked by
// simple keyword matching, never sent to a server or an AI service. It cannot search faces or
// read anyone's data; it only explains and points to pages.
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

export type HelpTopic =
  | 'find'
  | 'none'
  | 'notMe'
  | 'unclear'
  | 'passkey'
  | 'newPhone'
  | 'faceStored'
  | 'delete'
  | 'badLink';

/** Topics in the order they are offered, with words (English, Hindi, Hinglish) that point to them. */
const TOPICS: { id: HelpTopic; link?: string; words: string[] }[] = [
  { id: 'find', link: '/scan', words: ['find my', 'scan', 'dhund', 'khoj', 'खोज', 'स्कैन'] },
  {
    id: 'none',
    link: '/scan',
    words: [
      'no photo',
      'not found',
      'missing',
      'nahi mil',
      'nahi dikh',
      'zero',
      'empty',
      'नहीं मिल',
      'नहीं दिख',
      'कोई फ़ोटो',
    ],
  },
  {
    id: 'notMe',
    link: '/photos',
    words: ['not me', 'wrong', 'someone else', 'galat', 'mera nahi', 'मैं नहीं', 'गलत'],
  },
  {
    id: 'unclear',
    link: '/find',
    words: [
      'blur',
      'crowd',
      'unclear',
      'more photo',
      'find more',
      'dhundhl',
      'peeche',
      'धुंधल',
      'और फ़ोटो',
    ],
  },
  {
    id: 'passkey',
    link: '/',
    words: [
      'passkey',
      'sign in',
      'login',
      'log in',
      'password',
      'fingerprint',
      'पासकी',
      'लॉग इन',
      'साइन इन',
    ],
  },
  {
    id: 'newPhone',
    link: '/',
    words: [
      'new phone',
      'other phone',
      'laptop',
      'another device',
      'naya phone',
      'नया फ़ोन',
      'दूसरे फ़ोन',
    ],
  },
  {
    id: 'faceStored',
    link: '/privacy',
    words: ['store', 'save', 'safe', 'privacy', 'surakshit', 'सुरक्षित', 'प्राइवेसी'],
  },
  {
    id: 'delete',
    link: '/privacy',
    words: ['delete', 'remove', 'erase', 'account', 'hatao', 'mitao', 'हटा', 'मिटा', 'डिलीट'],
  },
  { id: 'badLink', words: ['qr', 'link', 'invite', 'expired', 'invalid', 'लिंक', 'क्यूआर'] },
];

/**
 * Purpose: pick the help topic that best fits a typed question (most matching words wins).
 * Inputs: the question as typed. Outputs: a topic, or null when nothing matches.
 * Side effects: none (runs only in the browser; nothing is sent anywhere).
 */
export function matchTopic(question: string): HelpTopic | null {
  const text = question.toLowerCase();
  let best: HelpTopic | null = null;
  let bestScore = 0;
  for (const topic of TOPICS) {
    const score = topic.words.filter((word) => text.includes(word)).length;
    if (score > bestScore) {
      best = topic.id;
      bestScore = score;
    }
  }
  return best;
}

type Message = { from: 'me'; text: string } | { from: 'bot'; topic: HelpTopic | null };

/**
 * Purpose: the help bubble and its chat panel (shown on every page of the site frame).
 * Inputs: none. Outputs: bubble + panel elements.
 * Side effects: none beyond local state (no network, no storage).
 */
export function HelpChat() {
  const { t } = useTranslation();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight; // newest answer in view
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  function ask(question: string, topic: HelpTopic | null) {
    setMessages((list) => [...list, { from: 'me', text: question }, { from: 'bot', topic }]);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const question = text.trim();
    if (!question) return;
    ask(question, matchTopic(question));
    setText('');
  }

  return (
    <>
      <button
        type="button"
        className="st-help-bubble"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? t('help.close') : t('help.open')}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? '×' : '?'}
      </button>
      {open && (
        <section id={panelId} className="st-help-panel" role="dialog" aria-label={t('help.title')}>
          <header className="st-help-head">
            <strong>{t('help.title')}</strong>
            <span>{t('help.subtitle')}</span>
          </header>
          <div className="st-help-log" ref={log} aria-live="polite">
            <p className="st-help-msg is-bot">{t('help.hello')}</p>
            {messages.map((message, i) =>
              message.from === 'me' ? (
                <p key={i} className="st-help-msg is-me">
                  {message.text}
                </p>
              ) : (
                <BotAnswer key={i} topic={message.topic} onLink={() => setOpen(false)} />
              ),
            )}
          </div>
          <div className="st-help-chips">
            {TOPICS.map((topic) => (
              <button
                key={topic.id}
                type="button"
                className="st-help-chip"
                onClick={() => ask(t(`help.q.${topic.id}`), topic.id)}
              >
                {t(`help.q.${topic.id}`)}
              </button>
            ))}
          </div>
          <form className="st-help-form" onSubmit={submit}>
            <input
              ref={input}
              className="st-input"
              value={text}
              maxLength={200}
              placeholder={t('help.placeholder')}
              aria-label={t('help.placeholder')}
              onChange={(event) => setText(event.target.value)}
            />
            <button type="submit" className="st-btn st-btn-primary st-btn-small">
              {t('help.send')}
            </button>
          </form>
        </section>
      )}
    </>
  );
}

/**
 * Purpose: one bot answer with a button to the page that solves it.
 * Inputs: topic (null = not understood), close callback. Outputs: answer element.
 * Side effects: none.
 */
function BotAnswer({ topic, onLink }: { topic: HelpTopic | null; onLink: () => void }) {
  const { t } = useTranslation();
  if (topic === null) return <p className="st-help-msg is-bot">{t('help.unknown')}</p>;
  const link = TOPICS.find((item) => item.id === topic)?.link;
  return (
    <div className="st-help-msg is-bot">
      <p>{t(`help.a.${topic}`)}</p>
      {link && (
        <Link to={link} className="st-btn st-btn-ghost st-btn-small" onClick={onLink}>
          {t(`help.go.${topic}`)}
        </Link>
      )}
    </div>
  );
}
