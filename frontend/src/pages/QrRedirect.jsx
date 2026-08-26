import { useEffect, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const FALLBACK_URL = '/menu/hong-duck';

export default function QrRedirect() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const spot = (params.get('spot') || 'default')
      .toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 64) || 'default';

    fetch(`${API_URL}/api/public/qr/hong-duck?spot=${encodeURIComponent(spot)}`)
      .then((response) => {
        if (!response.ok) throw new Error('QR resolve failed');
        return response.json();
      })
      .then(({ target_url: targetUrl }) => window.location.replace(targetUrl || FALLBACK_URL))
      .catch(() => {
        setFailed(true);
        window.location.replace(FALLBACK_URL);
      });
  }, []);

  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24, background: '#9e0c0b', color: '#fff8e9', fontFamily: '"Noto Sans Thai", sans-serif', textAlign: 'center' }}>
      <div>
        <strong style={{ display: 'block', fontSize: 22 }}>{failed ? 'กำลังเปิดเมนูโดยตรง' : 'กำลังเปิดเมนู…'}</strong>
        <a href={FALLBACK_URL} style={{ display: 'inline-block', marginTop: 12, color: '#ffbf45' }}>แตะที่นี่หากหน้าไม่เปิด</a>
      </div>
    </main>
  );
}
