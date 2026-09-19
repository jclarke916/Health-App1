// Cloudflare Worker: CORS proxy for the Oura API, used by Centurion.
//
// Replaces the original open proxy, which would fetch ANY url for ANY website.
// This one only forwards GETs to api.ouraring.com/v2/usercollection/* and only
// answers the origins listed below. It never sees or stores anything but the
// request passing through; the Oura token stays in the caller's Authorization header.
//
// Deploy: Cloudflare dashboard -> Workers & Pages -> oura-proxy -> Edit code ->
// paste this file -> Deploy. The app needs no change (same ?url= interface).

const ALLOWED_ORIGINS = [
  'https://jclarke916.github.io',
  'http://localhost:8765', // local dev server (.claude/launch.json)
];
const OURA_PREFIX = 'https://api.ouraring.com/v2/usercollection/';

function cors(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Headers': 'Authorization',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

export default {
  async fetch(request) {
    const origin = request.headers.get('Origin') || '';
    if (!ALLOWED_ORIGINS.includes(origin)) {
      return new Response('Forbidden origin', { status: 403 });
    }
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors(origin) });
    }
    if (request.method !== 'GET') {
      return new Response('Method not allowed', { status: 405, headers: cors(origin) });
    }

    const target = new URL(request.url).searchParams.get('url') || '';
    let parsed;
    try { parsed = new URL(target); } catch (e) { parsed = null; }
    // Compare the normalised href so tricks like "https://api.ouraring.com@evil.com" fail.
    if (!parsed || !parsed.href.startsWith(OURA_PREFIX)) {
      return new Response('Only the Oura usercollection API is allowed', { status: 400, headers: cors(origin) });
    }

    const auth = request.headers.get('Authorization');
    if (!auth) {
      return new Response('Missing Authorization header', { status: 401, headers: cors(origin) });
    }

    const upstream = await fetch(parsed.href, { headers: { Authorization: auth } });
    const headers = new Headers(cors(origin));
    headers.set('Content-Type', upstream.headers.get('Content-Type') || 'application/json');
    headers.set('Cache-Control', 'no-store');
    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
