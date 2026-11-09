// AppLayout: header with home and privacy links, a language toggle (English/Hindi) and sign-out.
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/apiClient';

export function AppLayout() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();

  function toggleLanguage() {
    void i18n.changeLanguage(i18n.language === 'hi' ? 'en' : 'hi');
  }

  async function signOut() {
    try {
      await api('/auth/logout', { method: 'POST' });
    } finally {
      navigate('/');
    }
  }

  return (
    <>
      <header className="topbar">
        <Link to="/">{t('app.name')}</Link>
        <nav>
          <Link to="/privacy">{t('common.privacy')}</Link>
          <button type="button" className="link" onClick={toggleLanguage}>
            {t('common.language')}
          </button>
          <button type="button" className="link" onClick={() => void signOut()}>
            {t('common.signOut')}
          </button>
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}
