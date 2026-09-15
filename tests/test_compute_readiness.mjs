import test from 'node:test';
import assert from 'node:assert/strict';
import {computeReadiness} from '../public/fork-microscope/compute-readiness.mjs';
test('disconnection does not reuse stale GPU information',()=>{assert.equal(computeReadiness(false,{cuda_available:true}).kind,'offline');});
test('missing telemetry is unknown, not CPU',()=>{assert.equal(computeReadiness(true,{}).kind,'unknown');});
test('CUDA selection on a CPU runtime blocks loading',()=>{assert.equal(computeReadiness(true,{cuda_available:false},'cuda').blocked,true);});
test('CPU selection warns even when a GPU exists',()=>{assert.equal(computeReadiness(true,{cuda_available:true},'cpu').kind,'cpu');});
test('loaded CPU model takes precedence over newly selected GPU',()=>{assert.equal(computeReadiness(true,{cuda_available:true},'cuda',{device:'cpu'}).kind,'cpu');});
test('available GPU is not a memory fit guarantee',()=>{const v=computeReadiness(true,{cuda_available:true,gpu_name:'Test',system_memory_gb:16});assert.equal(v.kind,'gpu');assert.match(v.message,/does not guarantee/);assert.match(v.summary,/16.0 GB/);});
