// Independently published SDK schemas, not a duplicate of XDOG's validator.
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
const root=join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT,'node_modules/@modelcontextprotocol/core');
const pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
if(pkg.version!=='2.3.1') throw new Error('Pinned independent SDK 2.3.1 required');
const core=await import(pathToFileURL(join(root,pkg.exports['./internal'].import.default)));
const annotations={audience:['user','assistant'],priority:0.5,lastModified:'2024-01-01T12:30:00+01:00'};
const valid=[{type:'text',text:'SDK-TEXT',annotations,_meta:{'vendor/opaque':[false,null,0]}},
  {type:'audio',data:'AAECAw==',mimeType:'audio/wav',annotations},
  {type:'audio',data:'',mimeType:'audio/wav',annotations},
  {type:'image',data:'',mimeType:'image/png',annotations},
  {type:'resource',resource:{uri:'test://empty',blob:''},annotations},
  {type:'resource',resource:{uri:'test://document',text:'SDK-RESOURCE',_meta:{'vendor/nested':true}},annotations},
  {type:'resource',resource:{uri:'test://binary',blob:'AAECAw==',mimeType:'application/octet-stream'},annotations},
  {type:'resource_link',name:'SDK link',uri:'https://must-not-fetch.invalid',size:4,annotations}];
const invalid=[{type:'audio',data:'bad!',mimeType:'audio/wav'},
  {type:'audio',data:'AA==',mimeType:'audio/wav',annotations:{audience:['system']}},
  {type:'text',text:'x',annotations:{priority:1.1}},
  {type:'text',text:'x',annotations:{lastModified:'not a date'}}];
for(const value of invalid) if(core.ContentBlockSchema.safeParse(value).success)
  throw new Error('Independent SDK unexpectedly accepts invalid corpus member');
console.log(JSON.stringify({valid:valid.map(value=>core.ContentBlockSchema.parse(value)),invalid}));
