import {expect,it,vi} from 'vitest'
import {ApiError} from './api'
import {saveThenExport} from './exportWorkflow'
it('saves unsaved edits before export and retries a revision conflict',async()=>{const calls:string[]=[];const api={save:vi.fn(async(_id:string,revision:number)=>{calls.push(`save:${revision}`);if(revision===2)throw new ApiError(409,'conflict');return {revision:4}}),getDraft:vi.fn(async()=>({revision:3})),export:vi.fn(async()=>{calls.push('export');return {version_id:'v1'}})};const result=await saveThenExport(api,{id:'d',revision:2},[{id:'x',seq:1,type:'cover_region',page_index:0,bbox:[1,1,2,2]}]);expect(calls).toEqual(['save:2','save:3','export']);expect(result).toEqual({revision:4,version_id:'v1'})})
