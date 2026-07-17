import argparse
from pathlib import Path
import fitz

parser=argparse.ArgumentParser();parser.add_argument("output",nargs="?",type=Path,default=Path(__file__).parents[1]/"sample.pdf");args=parser.parse_args()
out=args.output;out.parent.mkdir(parents=True,exist_ok=True)
doc=fitz.open();page=doc.new_page(width=595,height=842)
page.draw_rect(fitz.Rect(45,45,550,797),color=(0.15,0.2,0.17),width=1)
page.insert_text((72,95),"COMMUNITY WORKSHOP NOTES",fontname="helv",fontsize=18,color=(0.1,0.15,0.12))
page.insert_text((72,135),"Topic: Neighborhood garden planning",fontsize=12)
page.insert_text((72,165),"Date: 18 July 2026",fontsize=12)
page.insert_text((72,220),"Bring sketches, seed lists, and practical questions.",fontsize=11)
page.insert_text((72,250),"This is a synthetic, non-sensitive demonstration PDF.",fontsize=11)
doc.save(out);doc.close();print(out)
