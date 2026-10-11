// Construct valid cases through the independently published serving SDK validators.
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
const root=join(process.env.XDOG_MCP_OFFICIAL_SDK_ROOT,'node_modules/@modelcontextprotocol/core');
const pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
if(pkg.version!=='2.3.1') throw new Error('Pinned independent SDK 2.3.1 required');
const core=await import(pathToFileURL(join(root,pkg.exports['./internal'].import.default)));
const meta={'io.modelcontextprotocol/subscriptionId':-1};
const acknowledgement=core.SubscriptionsAcknowledgedNotificationSchema.parse({
  method:'notifications/subscriptions/acknowledged',params:{_meta:meta,notifications:{
    resourceSubscriptions:['test://watched'],toolsListChanged:false,promptsListChanged:false,resourcesListChanged:false}}});
const result=core.SubscriptionsListenResultSchema.parse({_meta:meta});
console.log(JSON.stringify({acknowledgement:{jsonrpc:'2.0',...acknowledgement},
                           completion:{jsonrpc:'2.0',id:-1,result}}));
