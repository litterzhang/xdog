// Opt-in independent SDK peer, never downloaded or activated by the application.
import {readFileSync, appendFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const root = join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT, 'node_modules/@modelcontextprotocol/server');
const pkg = JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
if (pkg.version !== '2.3.1') throw new Error('Pinned independent SDK 2.3.1 required');
const sdk = await import(pathToFileURL(join(root,pkg.exports['.'].import.default)));
const stdio = await import(pathToFileURL(join(root,pkg.exports['./stdio'].import.default)));
const {z} = createRequire(join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT,'package.json'))('zod');
const mode = process.argv[2], log = process.argv[3];
const record = (type, details={}) => appendFileSync(log,JSON.stringify({type,...details})+'\n');
let handle, http;
function factory() {
  const server = new sdk.McpServer({name:'official-sdk-peer',version:'1.0.0'},
    {capabilities:{resources:{subscribe:true}}});
  server.registerResource('watched','test://watched',{mimeType:'text/plain'}, async uri => {
    record('resource-read');
    return {contents:[{uri:uri.href,text:'must-not-read'}]};
  });
  server.registerResource('document',new sdk.ResourceTemplate('test://document/{name}',{
    list:undefined,complete:{name:async value => {record('completion',{kind:'template',value});return ['SDK-DOCUMENT'];}}
  }),{mimeType:'text/plain'},async (uri,variables) => {
    record('template-read');
    return {contents:[{uri:uri.href,text:'SDK-DOCUMENT:'+variables.name}]};
  });
  server.registerPrompt('review',{argsSchema:z.object({topic:sdk.completable(z.string(),async value => {
    record('completion',{kind:'prompt',value});return ['SDK-TOPIC','第二项'];
  })})},({topic}) => {
    record('prompt-get');
    return {messages:[{role:'user',content:{type:'resource_link',name:'related document',
      uri:'https://must-not-fetch.invalid/'+topic,title:'Untrusted related document',mimeType:'text/plain',
      description:'SDK-RESOURCE-LINK',size:123,_meta:{'fixture/private':'retained metadata'}}}]};
  });
  server.registerTool('diagnostic',{inputSchema:{},outputSchema:z.object({ok:z.boolean()})},async () => {
    record('diagnostic');
    return {isError:true,content:[{type:'text',text:'SDK-DIAGNOSTIC-FAILED'},
      {type:'image',mimeType:'image/png',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='}],
      structuredContent:{recovery:'SDK-STRUCTURED-ERROR-TAIL',retry:false}};
  });
  server.registerTool('content',{inputSchema:{}},async () => {
    record('content');
    return {content:[{type:'text',text:'SDK-CONTENT-TEXT',annotations:{audience:['assistant'],priority:0.5,
      lastModified:'2024-01-01T00:00:00Z'},_meta:{'fixture/provenance':'SDK-CONTENT-METADATA'}},
      {type:'audio',data:'UklGRg==',mimeType:'audio/wav',_meta:{'fixture/audio':'SDK-CONTENT-AUDIO'}},
      {type:'resource',resource:{uri:'test://embedded',blob:'AAECAw==',mimeType:'application/octet-stream',
        _meta:{'fixture/inner':'SDK-CONTENT-INNER'}},_meta:{'fixture/outer':'SDK-CONTENT-OUTER'}},
      {type:'resource_link',name:'linked data',uri:'https://must-not-fetch.invalid/document',
        _meta:{'fixture/link':'SDK-CONTENT-LINK'}}]};
  });
  return server;
}
if (mode === 'stdio') {
  const transport = new stdio.StdioServerTransport();
  const send = transport.send.bind(transport);
  transport.send = async message => {
    record('outbound',{message}); await send(message);
  };
  handle = stdio.serveStdio(factory,{transport});
  const previous = transport.onmessage;
  transport.onmessage = (...args) => {
    record('inbound',{method:args[0].method,params:args[0].params});
    return previous?.(...args);
  };
} else {
  handle = sdk.createMcpHandler(factory,{keepAliveMs:0});
  http = createServer(async (req,res) => {
    record('http-request',{method:req.method});
    const chunks=[];
    for await (const chunk of req) chunks.push(chunk);
    const body=Buffer.concat(chunks);
    if (body.length) record('inbound',{method:JSON.parse(body).method,params:JSON.parse(body).params});
    const controller=new AbortController();
    res.on('close',()=>controller.abort());
    const request=new Request('http://127.0.0.1'+req.url,{method:req.method,headers:req.headers,
      ...(body.length?{body}:{}),signal:controller.signal});
    try {
      const response=await handle.fetch(request);
      res.writeHead(response.status,Object.fromEntries(response.headers));
      res.flushHeaders();
      if (response.body) for await (const chunk of response.body) res.write(chunk);
      res.end();
    } catch (error) {res.destroy();}
  });
  http.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({port:http.address().port})));
}
