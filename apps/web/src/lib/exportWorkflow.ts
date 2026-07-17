import {ApiError} from './api'
import type {Operation} from '../types'

interface ExportApi{save:(id:string,revision:number,ops:Operation[])=>Promise<{revision:number}>;getDraft:(id:string)=>Promise<{revision:number}>;export:(id:string)=>Promise<{version_id:string}>}
export async function saveThenExport(client:ExportApi,draft:{id:string;revision:number},ops:Operation[]){
 let revision=draft.revision
 try{revision=(await client.save(draft.id,revision,ops)).revision}
 catch(error){
  if(!(error instanceof ApiError)||error.status!==409)throw error
  revision=(await client.getDraft(draft.id)).revision
  revision=(await client.save(draft.id,revision,ops)).revision
 }
 const result=await client.export(draft.id)
 return {revision,version_id:result.version_id}
}
