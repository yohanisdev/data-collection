// Same-origin API proxy. Browser cookies stay scoped to the Pages hostname;
// the Pages Function forwards them to the private-by-convention Flask origin.
export async function onRequest({ request, env, params }) {
  let origin;
  try {
    origin = new URL(env.API_ORIGIN);
  } catch {
    return Response.json({ error: 'API proxy is not configured.' }, { status: 503 });
  }
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
    return Response.json({ error: 'API proxy configuration is invalid.' }, { status: 503 });
  }
  if (typeof env.EDGE_PROXY_SECRET !== 'string' || env.EDGE_PROXY_SECRET.length < 32) {
    return Response.json({ error: 'API proxy is not configured.' }, { status: 503 });
  }

  const upstream = new URL(origin);
  const path = Array.isArray(params.path) ? params.path.join('/') : (params.path || '');
  upstream.pathname = `/api/${path}`;
  upstream.search = new URL(request.url).search;

  try {
    const headers = new Headers(request.headers);
    headers.set('X-Deployment-Proxy', env.EDGE_PROXY_SECRET);
    const clientIp = request.headers.get('CF-Connecting-IP');
    if (clientIp) headers.set('X-Pilot-Client-IP', clientIp);
    const upstreamRequest = new Request(upstream, request);
    const forwardedRequest = new Request(upstreamRequest, { headers });
    return await fetch(forwardedRequest);
  } catch {
    return Response.json({ error: 'The API is temporarily unavailable.' }, { status: 502 });
  }
}
