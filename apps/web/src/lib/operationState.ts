import type {Layout,Operation,TextStyle,VisualElement} from '../types'

const fallback:TextStyle={font_family:'helv',font_size_pt:12,color:'#111111',align:'left',rotation:0}

export function deriveElements(layouts:Layout[],ops:Operation[]):VisualElement[]{
 const state=new Map<string,VisualElement>()
 for(const l of layouts)for(const e of l.elements)state.set(e.id,{id:e.id,page_index:e.page_index,text:e.text,bbox:[...e.bbox],style:{...fallback,font_size_pt:e.font_size,color:e.color},kind:'native',deleted:false,changed:false,editability:e.editability})
 for(const op of [...ops].sort((a,b)=>a.seq-b.seq)){
  if(op.type==='cover_region')continue
  if(op.type==='add_text'){
   state.set(op.created_element_id!,{id:op.created_element_id!,page_index:op.page_index,text:op.payload?.text??'',bbox:[...op.bbox!],style:op.style??fallback,kind:'added',deleted:false,changed:true,editability:'native'});continue
  }
  const e=state.get(op.target_element_id!);if(!e||e.deleted)continue
  e.changed=true
  if(op.type==='delete_text')e.deleted=true
  if(op.type==='move_text'&&op.bbox)e.bbox=[...op.bbox]
  if(op.type==='replace_text'){if(op.payload?.text!==undefined)e.text=op.payload.text;if(op.bbox)e.bbox=[...op.bbox];if(op.style)e.style=op.style}
 }
 return [...state.values()]
}

export const covers=(ops:Operation[])=>ops.filter(o=>o.type==='cover_region'&&o.bbox).map(o=>({id:o.id,page_index:o.page_index,bbox:o.bbox!,color:o.payload?.cover_color??'#FFFFFF'}))
