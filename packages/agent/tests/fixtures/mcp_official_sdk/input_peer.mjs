// Owned opt-in independent SDK 2.3.1 modern input-required peer. No external I/O.
import {readFileSync, appendFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createServer} from 'node:http';
const root = join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT, 'node_modules/@modelcontextprotocol/server');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
if (pkg.version !== '2.3.1') throw new Error('Pinned independent SDK 2.3.1 required');
const sdk = await import(pathToFileURL(join(root, pkg.exports['.'].import.default)));
const stdio = await import(pathToFileURL(join(root, pkg.exports['./stdio'].import.default)));
const [mode, log] = process.argv.slice(2);
const record = (type, details = {}) => appendFileSync(log, JSON.stringify({type, ...details}) + '\n');
const STATE = '  opaque\u0000state/汉字\n  ';
function requestInput(family, ctx, finish) {
  const responses = ctx.mcpReq.inputResponses;
  const state = ctx.mcpReq.requestState();
  record('operation', {family, responses, state});
  if (responses?.review !== undefined) {
    if (state !== STATE) throw new Error('Opaque state was not echoed exactly');
    return finish('APPROVED-' + family);
  }
  const input = sdk.inputRequired.listRoots();
  return sdk.inputRequired({inputRequests: {review: input}, requestState: STATE});
}
function factory() {
  const server = new sdk.McpServer({name: 'official-input-peer', version: '1.0.0'});
  for (const family of ['roots']) {
    server.registerTool(family, {inputSchema: {}}, async (_args, ctx) =>
      requestInput(family, ctx, text => ({content: [{type: 'text', text}]})));
    server.registerResource(family, 'test://' + family, {mimeType: 'text/plain'}, async (uri, ctx) =>
      requestInput(family, ctx, text => ({contents: [{uri: uri.href, text}]})));
    server.registerPrompt(family, {}, async ctx =>
      requestInput(family, ctx, text => ({messages: [{role: 'user', content: {type: 'text', text}}]})));
  }
  return server;
}
if (mode === 'stdio') {
  const transport = new stdio.StdioServerTransport();
  const send = transport.send.bind(transport);
  transport.send = async message => {record('outbound', {message}); await send(message);};
  stdio.serveStdio(factory, {transport});
  const previous = transport.onmessage;
  transport.onmessage = (...args) => {record('inbound', {message: args[0]}); return previous?.(...args);};
} else {
  const handle = sdk.createMcpHandler(factory, {keepAliveMs: 0});
  const http = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (body.length) record('inbound', {message: JSON.parse(body), headers: req.headers});
    const controller = new AbortController();
    res.on('close', () => controller.abort());
    try {
      const response = await handle.fetch(new Request('http://127.0.0.1' + req.url, {
        method: req.method, headers: req.headers, ...(body.length ? {body} : {}), signal: controller.signal}));
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.flushHeaders();
      if (response.body) for await (const chunk of response.body) res.write(chunk);
      res.end();
    } catch {res.destroy();}
  });
  http.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port: http.address().port})));
}
