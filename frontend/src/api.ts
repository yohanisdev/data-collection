const base = import.meta.env.VITE_API_URL || '';
let csrf = '';
export function setCsrfToken(token:string){ csrf=token; }
export async function api<T=any>(path:string, options:RequestInit={}):Promise<T>{
  if(!csrf && path !== '/api/csrf') { const r=await fetch(base+'/api/csrf',{credentials:'include'}); if(r.ok) csrf=(await r.json()).csrf_token; }
  const headers=new Headers(options.headers); if(options.body) headers.set('Content-Type','application/json'); if(csrf) headers.set('X-CSRF-Token',csrf);
  const res=await fetch(base+path,{...options,headers,credentials:'include'}); const type=res.headers.get('content-type')||'';
  if(!res.ok){const data=type.includes('json')?await res.json():{}; throw new Error(data.error||`Request failed (${res.status})`)}
  if(type.includes('text/csv')) return await res.text() as T;
  const data=res.status===204?undefined:await res.json();
  if(data && typeof data.csrf_token==='string') csrf=data.csrf_token;
  return data as T;
}
