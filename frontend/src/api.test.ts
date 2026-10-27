import {afterEach,describe,expect,it,vi} from 'vitest';

afterEach(()=>{vi.unstubAllGlobals();vi.resetModules()});

describe('typed API client',()=>{
  it('loads a CSRF token and sends it with same-origin credentials',async()=>{
    const calls:any[]=[];
    vi.stubGlobal('fetch',vi.fn(async (url:string,init:any={})=>{
      calls.push([url,init]);
      if(url==='/api/csrf')return new Response(JSON.stringify({csrf_token:'token-1'}),{status:200,headers:{'content-type':'application/json'}});
      return new Response(JSON.stringify({status:'ok'}),{status:200,headers:{'content-type':'application/json'}});
    }));
    const {api}=await import('./api');
    expect(await api('/api/health')).toEqual({status:'ok'});
    expect(calls[1][1].credentials).toBe('include');
    expect(calls[1][1].headers.get('X-CSRF-Token')).toBe('token-1');
  });

  it('surfaces predictable API error messages',async()=>{
    vi.stubGlobal('fetch',vi.fn(async (url:string)=>url==='/api/csrf'
      ?new Response(JSON.stringify({csrf_token:'token-1'}),{status:200,headers:{'content-type':'application/json'}})
      :new Response(JSON.stringify({error:'Assignment expired.'}),{status:400,headers:{'content-type':'application/json'}})));
    const {api}=await import('./api');
    await expect(api('/api/translations',{method:'POST',body:'{}'})).rejects.toThrow('Assignment expired.');
  });
});
