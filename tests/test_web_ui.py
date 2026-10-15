"""Exercise the real UI state machine without a browser or hardware."""
from pathlib import Path
import subprocess

import pytest


def test_cleanup_recovery_and_bot_read_failure_messages():
    script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function node() {
  return {value:'',dataset:{},children:[],hidden:false,scrollHeight:0,scrollTop:0,clientHeight:0,
    handlers:{},setAttribute(){},replaceChildren(...c){this.children=c},append(...c){this.children.push(...c)},
    addEventListener(type,fn){this.handlers[type]=fn}};
}
const clean = {configured:true,connected:true,enabled:false,phase:'idle',cleanup_pending:false,messages:[]};
const failed = {...clean,phase:'error',cleanup_pending:true,error:'释放失败'};
let current = failed;
const requests = [];
const ctx = vm.createContext({AbortController,console,setTimeout(){return 1},clearTimeout(){},
  document:{getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},
    createElement:node,createDocumentFragment:node,createTextNode:text=>({textContent:text})},
  fetch:async(path,options)=>{requests.push(path);if(path==='/api/stop')current=clean;
    if(path==='/api/bots')return {ok:false,json:async()=>({detail:'Grok Bot 读取 Bot 列表失败（HTTP 417）。'})};
    return {ok:true,json:async()=>current};}});
vm.runInContext(fs.readFileSync('web/app.js','utf8'),ctx);
(async()=>{
  await new Promise(setImmediate);
  assert.equal(nodes.get('main-button').children[0].textContent,'重试释放语音');
  assert.equal(nodes.get('main-button').disabled,false);
  await nodes.get('main-button').handlers.click();
  assert.equal(requests.filter(path=>path==='/api/stop').length,1);
  assert.equal(requests.filter(path=>path==='/api/start').length,0);
  assert.equal(nodes.get('error').hidden,true);
  assert.equal(nodes.get('main-button').children[0].textContent,'开始对话');
  await vm.runInContext('loadBots()',ctx);
  assert.match(nodes.get('bot-status').textContent,/HTTP 417/);
  assert.doesNotMatch(nodes.get('bot-status').textContent,/登录/);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], cwd=Path(__file__).parents[1],
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['read_recovers', 'read_fails', 'auth_fails', 'send_fails', 'cancelled'])
def test_browser_connection_failures_are_distinct_from_login_and_never_resend(scenario):
    script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const scenario = process.argv[1];
const nodes = new Map();
const calls = [];
function node() {
  return {value:'',dataset:{},children:[],scrollHeight:0,scrollTop:0,clientHeight:0,
    setAttribute(){},replaceChildren(...c){this.children=c},append(...c){this.children.push(...c)},
    addEventListener(){}};
}
const ctx = vm.createContext({AbortController,TypeError,console,
  setTimeout(){return 1},clearTimeout(){},
  document:{getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},
    createElement:node,createDocumentFragment:node,createTextNode:text=>({textContent:text})},
  fetch:async(path)=>{
    if(path==='/api/state')return {ok:true,json:async()=>({messages:[]})};
    calls.push(path);
    if(scenario==='auth_fails')return {ok:false,status:503,
      json:async()=>({detail:'读取 Bot 列表失败（HTTP 401）。请检查本机 Grok Bot 登录状态。'})};
    if(scenario==='cancelled'){const error=new Error('cancelled');error.name='AbortError';throw error;}
    if(scenario==='read_recovers' && calls.length===2)return {ok:true,json:async()=>({bots:[{id:'b1',name:'测试 Bot'}]})};
    throw new TypeError('Failed to fetch');
  }});
vm.runInContext(fs.readFileSync('web/app.js','utf8'),ctx);
(async()=>{
  await new Promise(setImmediate);
  if(scenario==='send_fails' || scenario==='cancelled') {
    await assert.rejects(vm.runInContext('api("/api/ask", {text:"test"})',ctx),
      error=>scenario==='cancelled' ? error.name==='AbortError' :
        /本机.*应用/.test(error.message) && /勿.*重发/.test(error.message));
    assert.equal(calls.length,1);
  } else {
    await vm.runInContext('loadBots()',ctx);
    const status=nodes.get('bot-status').textContent;
    if(scenario==='read_recovers')assert.match(status,/找到 1 个 Bot/);
    if(scenario==='read_fails'){
      assert.match(status,/本机.*应用/);
      assert.doesNotMatch(status,/登录|Failed to fetch/);
      assert.equal(nodes.get('refresh-bots').disabled,false);
    }
    if(scenario==='auth_fails')assert.match(status,/HTTP 401.*登录/);
    assert.equal(calls.length,scenario==='auth_fails'?1:2);
  }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script, scenario], cwd=Path(__file__).parents[1],
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
