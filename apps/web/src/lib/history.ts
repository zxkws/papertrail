import type {Operation} from '../types'
export interface History {past:Operation[][];present:Operation[];future:Operation[][]}
export const initialHistory:History={past:[],present:[],future:[]}
export function commit(h:History, ops:Operation[]):History{return {past:[...h.past,h.present],present:ops,future:[]}}
export function undo(h:History):History{if(!h.past.length)return h;return {past:h.past.slice(0,-1),present:h.past.at(-1)!,future:[h.present,...h.future]}}
export function redo(h:History):History{if(!h.future.length)return h;return {past:[...h.past,h.present],present:h.future[0],future:h.future.slice(1)}}

