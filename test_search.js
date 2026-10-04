const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/search.js','utf8');let loads=0,requests=0;
// Saved-source searches must run on filter changes without a second button.
let filterSearches=0;
vm.runInNewContext(source.match(/function requestSearch\(\)\{[^\n]+\}/)[0]+';requestSearch()',{search(){filterSearches++}});
assert.equal(filterSearches,1);
assert(!source.includes('external-search'));
const pageWindow=source.match(/const start=.*?,end=.*?;/)[0];
for(const [page,total,expected] of [[1,45,[1,10]],[10,45,[6,15]],[11,45,[7,16]],[45,45,[36,45]],[2,3,[1,3]]])assert.deepEqual(Array.from(vm.runInNewContext(pageWindow+'[start,end]',{data:{page,pages:total}})),expected);
const context=vm.createContext({URLSearchParams,AbortController,Promise,Map,document:{fonts:{add(){}}},FontFace:class{load(){loads++;return Promise.resolve(this)}},fetch:async()=>{requests++;return {ok:true,json:async()=>({page:1,faces:[]})}}});
vm.runInContext(source.slice(source.indexOf('const fontLoads='),source.indexOf('async function search(')),context);
(async()=>{await vm.runInContext("Promise.all([loadFont('same'),loadFont('same')])",context);assert.equal(loads,1);await vm.runInContext("getPage(new URLSearchParams('page=1'),new AbortController().signal)",context);await vm.runInContext("getPage(new URLSearchParams('page=1'),new AbortController().signal)",context);assert.equal(requests,1);assert.equal(vm.runInContext("for(let i=2;i<10;i++)cachePage('page='+i,{});pageCache.size",context),6);console.log('PASS: concurrent font deduplication, page cache reuse and bounded cache')})().catch(error=>{console.error(error);process.exitCode=1});
