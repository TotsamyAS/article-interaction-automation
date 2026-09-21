import { resolve, sep } from 'node:path';

interface FrontendConfig {
  host: string;
  port: number;
  backend_base_url: string;
}

const config = JSON.parse(await Bun.file(new URL('./config.json', import.meta.url)).text()) as FrontendConfig;
const dist = resolve(import.meta.dir, 'dist');
const indexFile = Bun.file(resolve(dist, 'index.html'));
const proxyPrefixes = ['/api', '/health', '/docs', '/openapi.json'];

function isProxyPath(pathname: string) {
  return proxyPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(prefix + '/'));
}

async function proxy(request: Request, url: URL) {
  const target = new URL(url.pathname + url.search, config.backend_base_url);
  const headers = new Headers(request.headers);
  headers.delete('host');
  return fetch(target, {
    method: request.method,
    headers,
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
    redirect: 'manual'
  });
}

Bun.serve({
  hostname: config.host,
  port: config.port,
  async fetch(request) {
    const url = new URL(request.url);
    if (isProxyPath(url.pathname)) return proxy(request, url);

    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const candidate = resolve(dist, relative || 'index.html');
    if (candidate === dist || candidate.startsWith(dist + sep)) {
      const file = Bun.file(candidate);
      if (await file.exists()) return new Response(file);
    }
    return new Response(indexFile, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
});

console.log(`Frontend listening on http://${config.host}:${config.port}`);
