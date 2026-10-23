"""Execute packaged scripts with iframe timers suspended; no network or accounts."""
import json
from pathlib import Path
import re
import subprocess
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import naia_tools_ui as ui

SCRIPT = r'''
const assert=require('node:assert/strict'), vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class El {
 constructor(tag){this.tagName=tag;this.style={};this.children=[];this.events={};this.src='';this.contentWindow={postMessage:(data,origin)=>this.messages.push({data,origin})};this.messages=[];}
 append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
 setAttribute(k,v){this[k]=v;} addEventListener(k,f){this.events[k]=f;}
 showModal(){this.open=true;} close(){this.open=false;}
}
(async()=>{
 const body=new El('body'), events={}, intervals=[];
 let installed;
 const doc={body,createElement:t=>new El(t),addEventListener:(k,f)=>events[k]=f,visibilityState:'visible'};
 const win={addEventListener:(k,f)=>events[k]=f,__naiaInstallScreenAgent:args=>installed=args,dispatchEvent(){}};
 vm.runInNewContext(input.injection,{window:win,document:doc,location:{origin:'http://127.0.0.1:9000'},URL,Map,JSON,setInterval:f=>{intervals.push(f);return 1;},clearInterval(){},setTimeout(){},clearTimeout(){}});
 const frame=installed.frame,dialog=body.children.find(x=>x.tagName==='dialog');
 assert.equal(frame.parentElement,body);assert.notEqual(frame.parentElement,dialog);
 assert.match(frame.src,/mode=transport/);assert.equal(dialog.open,undefined);
 intervals[0]();assert.equal(frame.messages.at(-1).data.type,'naia-transport-wake');
 events.click({target:{closest:()=>true},preventDefault(){}});
 assert.equal(dialog.open,true);assert.ok(dialog.children[1].src);assert.notEqual(dialog.children[1],frame);
 dialog.close();intervals[0]();assert.equal(frame.parentElement,body);
 for(const transport of [true,false]){
  const nodes=new Map(), listeners={},requests=[],out=[];
  const parent={postMessage:(d,o)=>out.push(d)};
  const document={body:{style:{}},getElementById:id=>{if(!nodes.has(id))nodes.set(id,{style:{},classList:{toggle(){}},addEventListener(){}});return nodes.get(id);}};
  const window={parent,addEventListener:(k,f)=>listeners[k]=f};
  const state={armed:true,extension_active:true,paired:true,settings:{plugin_enabled:true,allow_screen:true},memory:{},bridge:{},screen:{ready:true}};
  const context={window,document,location:{hash:'#token=fake&parent=http%3A%2F%2F127.0.0.1%3A9000'+(transport?'&mode=transport':''),pathname:'/'},history:{replaceState(){}},URL,URLSearchParams,crypto:require('node:crypto').webcrypto,Uint8Array,Map,Set,Date,JSON,AbortController,setInterval(){},setTimeout(){return 1;},clearTimeout(){},fetch:async path=>{requests.push(path);return{ok:true,json:async()=>path==='/status'?state:{commands:[]}};}};
  vm.runInNewContext(input.panel,context);
  for(let i=0;i<3;i++)await new Promise(setImmediate);
  await listeners.message({source:parent,origin:'http://127.0.0.1:9000',data:{type:'naia-ui-ready'}});
  for(let i=0;i<3;i++)await new Promise(setImmediate);
  requests.length=0;
  await listeners.message({source:parent,origin:'http://127.0.0.1:9000',data:{type:'naia-transport-wake'}});
  for(let i=0;i<3;i++)await new Promise(setImmediate);
  assert.equal(requests.includes('/screen/poll'),transport);
  requests.length=0;
  await listeners.message({source:{},origin:'https://invalid.example',data:{type:'naia-transport-wake'}});
  assert.equal(requests.length,0);
 }
 console.log('transport independent of closed settings, wake recovery without iframe timers, settings cannot claim screen, origin guards: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''

class TransportLifecycleTests(unittest.TestCase):
    def test_packaged_parent_and_panel_scripts(self):
        injection = re.findall(r'<script>(.*?)</script>', ui._injection('http://127.0.0.1:32101/#token=fake'), re.S)[-1]
        panel = re.findall(r'<script>(.*?)</script>', (ROOT / 'android_tools_panel.html').read_text(), re.S)[0]
        result = subprocess.run(['node', '-e', SCRIPT], input=json.dumps({'injection':injection,'panel':panel}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__':
    unittest.main()
