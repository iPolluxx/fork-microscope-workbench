import test from 'node:test';
import assert from 'node:assert/strict';
import {isSaveShortcut,parsePromptSetImport,validatePromptSet,batchReadiness} from '../public/fork-microscope/workspace.mjs';
test('save shortcut accepts either platform without capturing other shortcuts',()=>{
 for(const modifiers of [{ctrlKey:true},{metaKey:true}])assert.equal(isSaveShortcut({...modifiers,key:'s'}),true);
 for(const event of [{key:'s'},{ctrlKey:true,key:'p'},{ctrlKey:true,altKey:true,key:'s'},{metaKey:true,shiftKey:true,key:'s'}])assert.equal(isSaveShortcut(event),false);
});
test('offline portable authoring can round trip without any worker identity',()=>{
 const original={schema:'fork-microscope-prompt-set-v1',name:'Questions',prompts:[{id:'old',title:'Choice',prompt:'Choose A or B',answers:['A','B'],mode:'chat',max_tokens:64,seed:0}]};
 const imported=parsePromptSetImport(JSON.parse(JSON.stringify(original)),()=> 'new');
 assert.equal(validatePromptSet(imported),'');assert.equal(imported.prompts[0].id,'new');assert.equal(imported.id,undefined);
 assert.match(batchReadiness({connected:false,selectedCount:1}),/still prepare/);
 assert.equal(imported.prompts[0].prompt,original.prompts[0].prompt);
});
