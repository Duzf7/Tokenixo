#!/usr/bin/env python3
"""Tokenixo - Offline Token Counter | Duzf7 | MIT | github.com/Duzf7/Tokenixo
Dependencies: pip install tiktoken  OR  pip install tokenizers"""
VERSION="26.1.7"  # ← change version here; syncs everywhere

import sys,platform,subprocess,threading,tkinter as tk
from tkinter import filedialog
from bisect import bisect_left,bisect_right


def _load_tiktoken():
    import tiktoken  # type: ignore
    enc=tiktoken.get_encoding("cl100k_base")
    _ds=lambda d:sys.getsizeof(d)+sum(sys.getsizeof(k)+sys.getsizeof(v) for k,v in d.items())
    size=_ds(enc._mergeable_ranks)+(_ds(enc._special_tokens) if hasattr(enc,'_special_tokens') else 0)
    def token_spans(text):
        spans,off=[],0
        for tid in enc.encode(text): p=enc.decode([tid]); spans.append((off,off+len(p))); off+=len(p)
        return spans
    return enc.encode,token_spans,size

def _load_xenova():
    from transformers import GPT2TokenizerFast  # type: ignore
    try:    tok=GPT2TokenizerFast.from_pretrained('Xenova/claude-tokenizer',local_files_only=True)
    except: tok=GPT2TokenizerFast.from_pretrained('Xenova/claude-tokenizer')
    try:    size=len(tok.backend_tokenizer.to_str().encode())
    except: size=sum(sys.getsizeof(k)+sys.getsizeof(v) for k,v in tok.get_vocab().items())
    def token_spans(text):
        e=tok(text,return_offsets_mapping=True); ofs=e.get("offset_mapping",[])
        if ofs: return[(s,e) for s,e in ofs if e>s]
        spans,off=[],0
        for tid in e["input_ids"]: p=tok.decode([tid]); spans.append((off,off+len(p))); off+=len(p)
        return spans
    return tok.encode,token_spans,size

LOADERS=[("tiktoken (cl100k_base)",_load_tiktoken),("Xenova/claude-tokenizer",_load_xenova)]

def _line_idx(text):
    s,p=[0],0
    while(p:=text.find('\n',p))!=-1: p+=1; s.append(p)
    return s

def _lc(offs,ls):
    res,li,n=[],0,len(ls)
    for o in offs:
        while li+1<n and ls[li+1]<=o: li+=1
        res.append(f"{li+1}.{o-ls[li]}")
    return res

_T=[f"tok{i}" for i in range(6)]; _B=[f"tok{i}b" for i in range(6)]; _N=6
_LS=["#e8eeff","#fff8e0","#e2faf0","#fdeef8","#ededff","#fff5d0"]
_LB=["#bad0fc","#fce496","#9fedc8","#f7bedd","#c0caff","#faca4a"]
_DS=["#152645","#2a2300","#082416","#280b1a","#181636","#271e00"]
_DB=["#1a4f96","#6e5700","#0b6e35","#6e1a52","#28289a","#6e5700"]
# glass palette: (win, card, border, fg, sub)
_GL=("#f0f4fc","#ffffff","#d8e0f4","#1a1a2e","#6670a0")
_GD=("#13151f","#1e2130","#2e3348","#e0e4f4","#7880a8")
_CTX=[("Haiku 4.5",200_000),("Sonnet 4.6",1_000_000),("Opus 4.6",1_000_000)]

def _dark():
    try:
        r=subprocess.run(["defaults","read","-g","AppleInterfaceStyle"],capture_output=True,text=True)
        return r.returncode==0 and r.stdout.strip()=="Dark"
    except: return False


class App:
    def __init__(self):
        (self.toks,self.method,self.enc,self.sfn,self._load,self._done2,self._res2)={},None,None,None,True,False,None
        (self._spans,self._ss,self._ls,self._last,self._gen,self._vrange)=None,None,None,None,0,None
        self._rid=self._sid=None; self._dm=_dark() if platform.system()=="Darwin" else False
        r=self.root=tk.Tk(); r.title(f"Tokenixo v{VERSION}"); r.minsize(720,520)
        sw,sh=r.winfo_screenwidth(),r.winfo_screenheight()
        r.geometry(f"980x740+{(sw-980)//2}+{(sh-740)//2}"); r.wm_attributes("-alpha",0.96)
        r.grid_rowconfigure(2,weight=1); r.grid_columnconfigure(0,weight=1)
        self._ui(); self._theme(); self._poll()
        threading.Thread(target=self._loadtoks,daemon=True).start()

    def _ui(self):
        r=self.root
        top=self._top=tk.Frame(r); top.grid(row=0,column=0,sticky="ew",padx=12,pady=(12,0))
        top.grid_columnconfigure(1,weight=1)
        tk.Label(top,text="Tokenizer",font=("Menlo",12)).grid(row=0,column=0,padx=(12,6),pady=10)
        self.mv=tk.StringVar(value="Loading…")
        mm=self.mm=tk.OptionMenu(top,self.mv,"Loading…"); mm.configure(relief="flat",bd=0,highlightthickness=0,font=("Menlo",11))
        mm.grid(row=0,column=1,sticky="w",pady=8)
        self._b0=tk.Button(top,text="Clear",command=self._clear,relief="flat",bd=0,padx=10,pady=4,cursor="hand2",font=("Menlo",11))
        self._b0.grid(row=0,column=2,padx=4,pady=8)
        self._b1=tk.Button(top,text="Open…",command=self._open,relief="flat",bd=0,padx=10,pady=4,cursor="hand2",font=("Menlo",11))
        self._b1.grid(row=0,column=3,padx=(0,8),pady=8)
        self.st=tk.Label(r,text="Loading…",anchor="w",font=("Menlo",11))
        self.st.grid(row=1,column=0,sticky="ew",padx=16,pady=(6,2))
        inp=self._inp=tk.Frame(r); inp.grid(row=2,column=0,sticky="nsew",padx=12,pady=(0,6))
        inp.grid_rowconfigure(0,weight=1); inp.grid_columnconfigure(0,weight=1)
        ti=self.ti=tk.Text(inp,wrap=tk.WORD,font=("Menlo",13),relief="flat",bd=0,padx=14,pady=12,insertwidth=2)
        sb=tk.Scrollbar(inp,command=ti.yview); ti.configure(yscrollcommand=lambda *a:(sb.set(*a),self._svp()))
        ti.grid(row=0,column=0,sticky="nsew"); sb.grid(row=0,column=1,sticky="ns")
        for t in _T: ti.tag_configure(t)
        for t in _B: ti.tag_configure(t); ti.tag_raise(t)
        ti.bind("<Configure>",lambda e:self._svp())
        rf=self._rf=tk.Frame(r); rf.grid(row=3,column=0,sticky="ew",padx=12,pady=(0,6)); rf.grid_columnconfigure(list(range(5)),weight=1)
        self.rl={}
        for i,(d,k) in enumerate([("Tokens","tokens"),("Chars","chars"),("Words","words"),("Lines","lines"),("Ch/Tok","cpt")]):
            tk.Label(rf,text=d,font=("Menlo",10)).grid(row=0,column=i,pady=(10,0))
            lb=tk.Label(rf,text="—",font=("Menlo",18,"bold")); lb.grid(row=1,column=i,pady=(0,6)); self.rl[k]=lb
        cf=self._cf=tk.Frame(r); cf.grid(row=4,column=0,sticky="ew",padx=12,pady=(0,6)); cf.grid_columnconfigure(0,weight=1)
        tk.Label(cf,text="Context Window Usage",anchor="w",font=("Menlo",10,"bold")).grid(row=0,column=0,sticky="w",padx=12,pady=(8,4))
        self.cl={}
        for i,(n,_) in enumerate(_CTX):
            lb=tk.Label(cf,text="",anchor="w",font=("Menlo",11)); lb.grid(row=i+1,column=0,sticky="ew",padx=16,pady=1); self.cl[n]=lb
        self._ft=tk.Label(r,text=f"Tokenixo v{VERSION}  ·  Duzf7  ·  MIT",anchor="center",font=("Menlo",10))
        self._ft.grid(row=5,column=0,sticky="ew",padx=12,pady=(0,10))
        ti.bind("<KeyRelease>",lambda e:self._src()); ti.bind("<<Paste>>",lambda e:r.after(1,self._src))
        ti.bind("<<Cut>>",lambda e:r.after(1,self._src)); ti.bind("<<Selection>>",lambda e:self._sel())
        ti.bind("<ButtonRelease-1>",lambda e:r.after(10,self._sel))

    def _theme(self):
        g=_GD if self._dm else _GL; soft,bold=(_DS,_DB) if self._dm else (_LS,_LB)
        win,cd,brd,fg,sub=g
        self.root.configure(bg=win)
        for w in[self._top,self._inp,self._rf,self._cf]: w.configure(bg=cd,highlightbackground=brd,highlightthickness=1)
        self.ti.configure(bg=cd,fg=fg,insertbackground=fg,selectbackground=brd,selectforeground=fg)
        for w in[self.st,self._ft]: w.configure(bg=win,fg=sub)
        for p in[self._top,self._rf,self._cf]:
            for w in p.winfo_children():
                if isinstance(w,(tk.Label,tk.Button)): w.configure(bg=p["bg"],fg=fg,**({"activebackground":brd,"activeforeground":fg} if isinstance(w,tk.Button) else {}))
        self.mm.configure(bg=cd,fg=fg,activebackground=brd); self.mm["menu"].configure(bg=cd,fg=fg)
        for t,c in zip(_T,soft): self.ti.tag_configure(t,background=c)
        for t,c in zip(_B,bold): self.ti.tag_configure(t,background=c)
        self._vrange=None; self._vp()

    def _poll(self):
        cur=_dark() if platform.system()=="Darwin" else False
        if cur!=self._dm: self._dm=cur; self._theme()
        self.root.after(2000,self._poll)

    def _loadtoks(self):
        for n,L in LOADERS:
            try: e,s,z=L(); self.toks[n]=(e,s,z)
            except: pass
        self.root.after(0,self._ready)

    def _ready(self):
        self._load=False
        if not self.toks: self.st.config(text="ERROR: pip install tiktoken or transformers"); return
        self.method=list(self.toks.keys())[0]; self.enc,self.sfn,_=self.toks[self.method]
        mn=self.mm["menu"]; mn.delete(0,"end")
        for n in self.toks:
            _,_,z=self.toks[n]; mn.add_command(label=f"{n} — {z/1048576:.1f}MB",command=lambda x=n:self._sw(x))
        _,_,z=self.toks[self.method]; self.mv.set(f"{self.method} — {z/1048576:.1f}MB")
        self.st.config(text=f"Ready — {len(self.toks)} tokenizer(s)"); t=self.ti.get("1.0","end-1c")
        if t.strip(): self._count()

    def _sw(self,n):
        self.method=n; self.enc,self.sfn,_=self.toks[n]
        _,_,z=self.toks[n]; self.mv.set(f"{n} — {z/1048576:.1f}MB"); self._done2=False; self._res2=None; self._count()

    def _src(self):
        if self._rid: self.root.after_cancel(self._rid)
        self._rid=self.root.after(150,lambda:self._count() if self.ti.get("1.0","end-1c")!=self._last else None)

    def _svp(self):
        if self._sid: self.root.after_cancel(self._sid)
        self._sid=self.root.after(16,self._vp)

    def _tagg(self,tags,pairs,base=0):
        ti,ls=self.ti,self._ls
        if not pairs: return
        lcs=_lc([v for s,e in pairs for v in(s,e)],ls)
        bats=[[] for _ in range(_N)]
        for j in range(len(pairs)): bats[(base+j)%_N].append((lcs[2*j],lcs[2*j+1]))
        for ci,bat in enumerate(bats):
            if bat: ti.tk.call(ti._w,"tag","add",tags[ci],*[v for s,e in bat for v in(s,e)])

    def _vp(self):
        if not self._spans or not self._ls: return
        ti,ls,spans,ss=self.ti,self._ls,self._spans,self._ss
        fl,fc=map(int,ti.index("@0,0").split('.')); ll,lc=map(int,ti.index(f"@{ti.winfo_width()},{ti.winfo_height()}").split('.'))
        vs=ls[min(fl-1,len(ls)-1)]+fc; ve=ls[min(ll-1,len(ls)-1)]+lc
        fi=max(0,bisect_left(ss,vs)-200); li=min(len(spans),bisect_right(ss,ve)+200)
        if(fi,li)==self._vrange: return
        self._vrange=(fi,li)
        for t in _T: ti.tag_remove(t,"1.0",tk.END)
        vis=spans[fi:li]
        if vis: self._tagg(_T,vis,fi)

    def _bold(self,s0,e0):
        if not self._spans or not self._ls: return
        spans,ss=self._spans,self._ss
        fi=max(0,bisect_left(ss,s0)-1); li=bisect_right(ss,e0); pairs,idx=[],[]
        for i in range(fi,min(li,len(spans))):
            s,e=spans[i]
            if e<=s0 or s>=e0: continue
            pairs.append((max(s,s0),min(e,e0))); idx.append(i)
        if pairs:
            lcs=_lc([v for s,e in pairs for v in(s,e)],self._ls)
            bats=[[] for _ in range(_N)]
            for j,i in enumerate(idx): bats[i%_N].append((lcs[2*j],lcs[2*j+1]))
            for ci,bat in enumerate(bats):
                if bat: self.ti.tk.call(self.ti._w,"tag","add",_B[ci],*[v for s,e in bat for v in(s,e)])

    def _sel(self):
        if not self._done2 or not self.enc: return
        for t in _B: self.ti.tag_remove(t,"1.0",tk.END)
        try: sel=self.ti.get(tk.SEL_FIRST,tk.SEL_LAST)
        except tk.TclError:
            if self._res2: self._disp(*self._res2); return
            return
        if not sel.strip():
            if self._res2: self._disp(*self._res2); return
            return
        if self._ls:
            sl,sc=map(int,self.ti.index(tk.SEL_FIRST).split('.')); el,ec=map(int,self.ti.index(tk.SEL_LAST).split('.'))
            self._bold(self._ls[sl-1]+sc,self._ls[el-1]+ec)
        t=len(self.enc(sel)); c=len(sel)
        self._disp(t,c,len(sel.split()),sel.count('\n')+(1 if sel else 0),round(c/t,2) if t else 0,s=True)

    def _disp(self,tok,ch,w,ln,cpt,s=False):
        for k,v in[("tokens",f"{tok:,}"),("chars",f"{ch:,}"),("words",f"{w:,}"),("lines",f"{ln:,}"),("cpt",str(cpt))]:
            self.rl[k].config(text=v)
        for n,lim in _CTX: self.cl[n].config(text=f"  {n:12s}  {tok:>10,} / {lim:>10,}  ({tok/lim*100:.4f}%)")
        if s: self.st.config(text=f"Selection: {tok:,} tokens · {ch:,} chars")

    def _rmhl(self):
        for t in _T+_B: self.ti.tag_remove(t,"1.0",tk.END)
        self._vrange=None

    def _rst(self,wipe=False):
        self._done2=False; self._res2=None; self._spans=self._ss=self._ls=None
        for lb in self.rl.values(): lb.config(text="—")
        for lb in self.cl.values(): lb.config(text="")
        if wipe: self._last=None; self.ti.delete("1.0",tk.END)

    def _count(self):
        if self._load or not self.enc: return
        text=self.ti.get("1.0","end-1c")
        if not text.strip(): self._rmhl(); self._last=text; self._rst(); return
        self._gen+=1; gen,sfn=self._gen,self.sfn
        def work():
            try:    sp=sfn(text)
            except: sp=None
            if not sp: self.root.after(0,lambda:self._fin(gen,text,None,None,None)); return
            ls=_line_idx(text); ss=[s for s,_ in sp]
            self.root.after(0,lambda:self._fin(gen,text,sp,ss,ls))
        threading.Thread(target=work,daemon=True).start()

    def _fin(self,gen,text,sp,ss,ls):
        if gen!=self._gen: return
        self._spans,self._ss,self._ls,self._last=sp,ss,ls,text; self._vrange=None
        if not sp: self._rmhl(); self._done2=False; self._res2=None; return
        t=len(sp); c=len(text)
        self._res2=(t,c,len(text.split()),text.count('\n')+(1 if text else 0),round(c/t,2) if t else 0)
        self._done2=True; self._rmhl(); self._vp(); self._disp(*self._res2)
        self.st.config(text=f"{t:,} tokens · {c:,} chars · {self.method}")

    def _clear(self):
        self._gen+=1; self._rmhl(); self._rst(wipe=True)
        self.st.config(text=f"Ready — {len(self.toks)} tokenizer(s)")

    def _open(self):
        p=filedialog.askopenfilename(filetypes=[("Text files","*.txt *.md *.py *.js *.ts *.json *.csv"),("All files","*.*")])
        if not p: return
        try:
            with open(p,'r',encoding='utf-8',errors='replace') as f: c=f.read()
            self.ti.delete("1.0",tk.END); self.ti.insert("1.0",c); self._count()
        except Exception as e:
            self.ti.delete("1.0",tk.END); self.ti.insert("1.0",f"Error: {e}")

    def run(self): self.root.mainloop()


if __name__=="__main__": App().run()
