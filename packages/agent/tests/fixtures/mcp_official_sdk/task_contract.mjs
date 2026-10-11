// Independently published legacy vocabulary, not a serving SDK task runtime.
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
const root=join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT,'node_modules/@modelcontextprotocol/core');
const pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
if(pkg.version!=='2.3.1') throw new Error('Pinned SDK 2.3.1 required');
const core=await import(pathToFileURL(join(root,pkg.exports['./internal'].import.default)));
const data=JSON.parse(readFileSync(process.argv[2],'utf8'));
const counts={};
for(const row of data) {
  const schema=core[row.schema];
  if(!schema) throw new Error('Missing named schema '+row.schema);
  schema.parse(row.value);
  counts[row.schema]=(counts[row.schema]??0)+1;
}
console.log(JSON.stringify({version:pkg.version,validated:counts}));
