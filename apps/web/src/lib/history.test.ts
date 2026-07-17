import {describe,expect,it} from 'vitest'
import {commit,initialHistory,redo,undo} from './history'
import type {Operation} from '../types'
const op:Operation={id:'1',seq:1,type:'cover_region',page_index:0,bbox:[1,2,3,4]}
describe('history',()=>{it('undoes and redoes operation snapshots',()=>{const changed=commit(initialHistory,[op]);expect(undo(changed).present).toEqual([]);expect(redo(undo(changed)).present).toEqual([op])})})

