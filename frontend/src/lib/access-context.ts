// Only a public principal UUID is stored here. Credentials stay in HttpOnly cookies.
let context: string | null = null;
const key = 'experiment-access-context';

export function setAccessContext(value: string) {
  context = value;
  try { sessionStorage.setItem(key, value); } catch { /* In-memory context still isolates this tab. */ }
}

export function initializeAccessContext() {
  const url = new URL(location.href);
  const requested = url.searchParams.get('access');
  if (requested) {
    setAccessContext(requested);
    url.searchParams.delete('access');
    history.replaceState(history.state, '', url);
  } else {
    try { context = sessionStorage.getItem(key); } catch { context = null; }
  }
}

export function accessContext() { return context; }

export function downloadUrl(path: string) {
  if (!context) return path;
  return `${path}${path.includes('?') ? '&' : '?'}access=${encodeURIComponent(context)}`;
}
