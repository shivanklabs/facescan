// EditMode: edit the site in the browser and save straight into the code (dev server only;
// owner decision 13 Nov 2026). Turn it on with the Edit button, click any text or box:
// - text that comes from the language files can be rewritten (saved to en.json / hi.json);
// - the box's size can be changed by dragging its corner or typing values (saved to
//   src/styles/site-edits.css for its class, so every box of that kind changes).
// The save routes exist only on `vite` dev (vite.config.ts); the built website has no panel.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useTranslation } from 'react-i18next';

type Translations = { [key: string]: string | Translations };

const SIZE_PROPS = ['height', 'width', 'font-size', 'border-radius', 'padding'] as const;
type SizeProp = (typeof SIZE_PROPS)[number];

/**
 * Purpose: every language-file key whose text is exactly this text (to know what to save).
 * Inputs: the translations of one language, the visible text. Outputs: dotted keys.
 * Side effects: none.
 */
export function keysForText(bundle: Translations, text: string, prefix = ''): string[] {
  const found: string[] = [];
  for (const [key, value] of Object.entries(bundle)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') {
      if (value.trim() === text) found.push(path);
    } else {
      found.push(...keysForText(value, text, path));
    }
  }
  return found;
}

/**
 * Purpose: the CSS selector a size change is saved for: the element's own site classes
 *   (st-...), or the nearest parent that has one. Inputs: element.
 * Outputs: e.g. ".st-moment" or null when nothing on the way has a site class.
 * Side effects: none.
 */
export function selectorFor(element: Element | null): string | null {
  for (let el = element; el; el = el.parentElement) {
    const classes = [...el.classList].filter((c) => /^st-[a-z0-9-]+$/.test(c)).slice(0, 2);
    if (classes.length > 0) return classes.map((c) => `.${c}`).join('');
  }
  return null;
}

/**
 * Purpose: save one change through the dev server. Inputs: route and body.
 * Outputs: the server's message. Side effects: one POST (writes a source file).
 */
async function save(route: 'text' | 'style', body: object): Promise<string> {
  const response = await fetch(`/__edit/${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = (await response.json()) as { saved?: string; error?: string };
  if (!response.ok) throw new Error(data.error ?? 'not saved');
  return data.saved ?? 'saved';
}

/**
 * Purpose: the Edit button, the outline under the mouse, and the panel for the picked
 *   element (text + sizes, with a corner handle to drag the size).
 * Inputs: none. Outputs: Edit mode elements. Side effects: listens to clicks while on;
 *   saves source files through the dev server.
 */
export function EditMode() {
  const { i18n } = useTranslation();
  const [on, setOn] = useState(false);
  const [picked, setPicked] = useState<HTMLElement | null>(null);
  const [text, setText] = useState('');
  const [keys, setKeys] = useState<string[]>([]);
  const [sizes, setSizes] = useState<Record<SizeProp, string>>({
    height: '',
    width: '',
    'font-size': '',
    'border-radius': '',
    padding: '',
  });
  const [message, setMessage] = useState('');
  const [box, setBox] = useState<DOMRect | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const lang = i18n.language === 'hi' ? 'hi' : 'en';

  // While on: the next click on the page picks an element instead of using it.
  useEffect(() => {
    if (!on) return;
    const pick = (event: MouseEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (!target || panel.current?.contains(target) || target.closest('.st-edit-toggle')) return;
      event.preventDefault();
      event.stopPropagation();
      const own = target.innerText.trim();
      const bundle = i18n.getResourceBundle(lang, 'translation') as Translations;
      const style = getComputedStyle(target);
      setPicked(target);
      setText(own);
      setKeys(own ? keysForText(bundle, own) : []);
      setSizes({
        height: style.height,
        width: style.width,
        'font-size': style.fontSize,
        'border-radius': style.borderTopLeftRadius,
        padding: style.paddingTop,
      });
      setBox(target.getBoundingClientRect());
      setMessage('');
    };
    const done = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOn(false);
        setPicked(null);
        setBox(null);
      }
    };
    document.addEventListener('click', pick, true);
    document.addEventListener('keydown', done);
    return () => {
      document.removeEventListener('click', pick, true);
      document.removeEventListener('keydown', done);
    };
  }, [on, i18n, lang]);

  const selector = selectorFor(picked);

  async function saveText() {
    const key = keys[0];
    if (!key) return;
    try {
      setMessage(`Saved: ${await save('text', { lang, key, value: text })} (${key})`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'not saved');
    }
  }

  async function saveSize(prop: SizeProp) {
    if (!selector) return;
    try {
      setMessage(`Saved: ${await save('style', { selector, prop, value: sizes[prop] })}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'not saved');
    }
  }

  // Drag the corner handle: live preview on the picked element, then save width + height.
  function startDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!picked || !box) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const { width, height } = box;
    const move = (e: PointerEvent) => {
      const w = `${Math.max(20, Math.round(width + e.clientX - startX))}px`;
      const h = `${Math.max(20, Math.round(height + e.clientY - startY))}px`;
      picked.style.width = w;
      picked.style.height = h;
      setSizes((s) => ({ ...s, width: w, height: h }));
      setBox(picked.getBoundingClientRect());
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  return (
    <>
      <button
        type="button"
        className={on ? 'st-edit-toggle is-on' : 'st-edit-toggle'}
        onClick={() => {
          setOn(!on);
          setPicked(null);
          setBox(null);
        }}
      >
        {on ? 'Editing: click anything (Esc: done)' : 'Edit'}
      </button>
      {on && box && (
        <>
          <div
            className="st-edit-outline"
            style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
          />
          <button
            type="button"
            className="st-edit-handle"
            aria-label="Drag to resize"
            style={{ left: box.right - 7, top: box.bottom - 7 }}
            onPointerDown={startDrag}
          />
        </>
      )}
      {on && picked && (
        <div className="st-edit-panel" ref={panel}>
          <strong>{selector ?? picked.tagName.toLowerCase()}</strong>
          {keys.length > 0 ? (
            <label>
              <span>
                Text ({lang}.json: {keys[0]})
              </span>
              <textarea value={text} rows={3} onChange={(e) => setText(e.target.value)} />
              <button type="button" onClick={() => void saveText()}>
                Save text
              </button>
            </label>
          ) : (
            <p>This text is not from the language files (names, numbers or mixed text).</p>
          )}
          {selector ? (
            SIZE_PROPS.map((prop) => (
              <label key={prop} className="st-edit-row">
                <span>{prop}</span>
                <input
                  value={sizes[prop]}
                  onChange={(e) => {
                    const value = e.target.value;
                    setSizes((s) => ({ ...s, [prop]: value }));
                    picked.style.setProperty(prop, value);
                  }}
                />
                <button type="button" onClick={() => void saveSize(prop)}>
                  Save
                </button>
              </label>
            ))
          ) : (
            <p>No site class here to save sizes for.</p>
          )}
          {message && <p role="status">{message}</p>}
        </div>
      )}
    </>
  );
}
