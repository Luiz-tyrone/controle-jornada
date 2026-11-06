"""Controle de Jornada AGT LOG — primeira versão operacional.
Dependência: pymupdf. Banco local SQLite com publicação validada em pasta de rede.
"""
import calendar, unicodedata, contextlib, csv, datetime as dt, hashlib, json, os, re, shutil, sqlite3, sys, tempfile, tkinter as tk
from pathlib import Path
from difflib import SequenceMatcher
from tkinter import ttk, filedialog, messagebox, font as tkfont

try:
    import fitz
except ImportError:
    fitz = None

APP = 'Controle de Jornada AGT LOG'
SERVER = r'\\Servidor-agtlog\doc frota\CONTROLE DE JORNADA\DADOS DO NOVO SISTEMA QUE SUB PLANILHA'
LOCAL = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AGTLOG' / 'ControleJornada'
CONFIG = LOCAL / 'config.json'
DB = LOCAL / 'jornadas.db'
# Existe enquanto há alteração local que ainda não chegou ao servidor.
PENDING = LOCAL / 'envio_pendente.flag'
KEEP_VERSIONS = 20
COLUMNS = ['data','inicio','fim','movimento','parado','espera','status']
# Paleta do design (design/mockup.html): neutro + menta.
# Paleta da marca AGT LOG (logo): roxo #381E42 e amarelo #FFCE42. MINT = cor de destaque (amarelo).
INK='#381E42'; MUTED='#6E6274'; LINE='#E4DFE7'; BG='#F5F3F6'; CARD='#FFFFFF'; MINT='#FFCE42'
OK='#1A7F4B'; ERR='#B03D47'; WARN='#8A6400'
FONT='Segoe UI'; MONO='Consolas'
MESES=['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
RESUMO=[('direcao','Tempo direção'),('intervalo','Tempo intervalo'),('espera','Tempo espera'),('tdte','TD + TE'),('descanso','T descanso'),('he_direcao','HE em direção'),('he_espera','HE em T espera'),('he_total','Total hora extra')]


def _mix(a,b,t):
    a=[int(a[i:i+2],16) for i in (1,3,5)];b=[int(b[i:i+2],16) for i in (1,3,5)]
    return '#%02x%02x%02x'%tuple(round(x+(y-x)*t) for x,y in zip(a,b))


def fade(w,target,ms):
    """Transição suave de cores (hover dos botões), ~50 fps, sem overshoot."""
    if getattr(w,'_job',None):w.after_cancel(w._job)
    start={k:w.cget(k) for k in target};n=max(1,ms//20)
    def step(i=1):
        if not w.winfo_exists():return  # widget fechado durante a animação
        e=1-(1-i/n)**3
        w.configure(**{k:_mix(start[k],v,e) for k,v in target.items()})
        w._job=w.after(20,step,i+1) if i<n else None
    step()


def live_button(parent,text,command,primary=False):
    """Botão vivo (modelo 3): uma faixa amarela enche o botão da esquerda para a direita no hover."""
    rest=INK if primary else CARD; rest_fg='#FFFFFF' if primary else INK
    font=tkfont.Font(family=FONT+' Semibold',size=10)
    c=tk.Canvas(parent,width=font.measure(text)+28,height=font.metrics('linespace')+16,bg=rest,highlightthickness=0,bd=0,cursor='hand2',takefocus=1)
    fill=c.create_rectangle(0,0,0,0,width=0,fill=MINT);label=c.create_text(0,0,text=text,font=font)
    edge=c.create_rectangle(0,0,0,0,width=1,outline=rest if primary else LINE)
    c.text=text;c._p=0.0;c._goal=0.0;c._job=None;c._rest_fg=rest_fg;c._focus=False
    def draw(e=None):
        w,h=c.winfo_width(),c.winfo_height();p=c._p
        c.coords(fill,0,0,w*p,h);c.coords(label,w/2,h/2);c.coords(edge,0,0,w-1,h-1)
        c.itemconfig(label,fill=_mix(c._rest_fg,INK,min(1,p*1.6)))
        c.itemconfig(edge,outline=INK if c._focus else _mix(rest if primary else LINE,MINT,p),width=2 if c._focus else 1)
    def step():
        if not c.winfo_exists():return
        c._p+=(c._goal-c._p)*0.28
        if abs(c._goal-c._p)<0.01:c._p=c._goal
        draw();c._job=c.after(16,step) if c._p!=c._goal else None
    def go(goal):
        c._goal=goal
        if c._job is None and c.winfo_exists():step()
    def press(e):c.itemconfig(fill,fill='#F2BC1F');go(1)
    def release(e):
        c.itemconfig(fill,fill=MINT)
        if 0<=e.x<c.winfo_width() and 0<=e.y<c.winfo_height():command()
    def set_text(t):
        c.text=t;c.itemconfig(label,text=t);c.config(width=font.measure(t)+28)
    def set_fg(color):c._rest_fg=color;draw()
    def focus(on):c._focus=on;draw()
    c.set_text=set_text;c.set_fg=set_fg
    c.bind('<Configure>',draw);c.bind('<Enter>',lambda e:go(1));c.bind('<Leave>',lambda e:go(0))
    c.bind('<ButtonPress-1>',press);c.bind('<ButtonRelease-1>',release)
    c.bind('<Return>',lambda e:command());c.bind('<space>',lambda e:command())
    c.bind('<FocusIn>',lambda e:focus(True));c.bind('<FocusOut>',lambda e:focus(False))
    return c


def hover_rows(tree):
    """Destaca a linha sob o mouse (tag 'hover')."""
    def show(row):
        last=getattr(tree,'_hov','')
        if row==last:return
        if last and tree.exists(last):tree.item(last,tags=[t for t in tree.item(last,'tags') if t!='hover'])
        if row:tree.item(row,tags=list(tree.item(row,'tags'))+['hover'])
        tree._hov=row
    tree.tag_configure('hover',background=BG)  # criada primeiro = maior prioridade
    tree.bind('<Motion>',lambda e:show(tree.identify_row(e.y)))
    tree.bind('<Leave>',lambda e:show(''))


def seconds(t):
    if not t: return 0
    h,m,s = map(int,t.split(':'))
    return h*3600+m*60+s


def hhmmss(n):
    n=int(n)
    return f'{n//3600:02d}:{n%3600//60:02d}:{n%60:02d}'


def normalize(name):
    return re.sub(r'\s+', ' ', name).strip(' :-')




def driver_parts(raw):
    """Separa categoria, nome e códigos operacionais do relatório."""
    raw=normalize(raw or '')
    category,sep,name=raw.partition(':')
    if not sep: category='NÃO INFORMADO';name=raw
    # Remove do fim: código numérico, grupos entre () ou {} e siglas soltas (LJ, MR, PS, WG, OP).
    name=re.sub(r'\s*\d{4,8}\s*$','',name)
    while True:
        cut=re.sub(r'[\s\-]*(?:\([^()]*\)|\{[^{}]*\}|\b(?:LJ|MR|PS|WG|OP)\b)\s*$','',name,flags=re.I)
        if cut==name:break
        name=cut
    return normalize(name),normalize(category).upper()


def month_metrics(rows):
    """Espelha C13/C14/C16 e totais C409:C416 da aba RELATÓRIO.
    Tempos negativos são sinalizados, não usados em pagamento.
    """
    totals=dict(direcao=0,intervalo=0,espera=0,tdte=0,descanso=0,he_direcao=0,he_espera=0,he_total=0)
    issues=[]
    for date,start,end,movement,stopped,waiting,status in rows:
        mov=seconds(movement);par=seconds(stopped);wait=seconds(waiting)
        # Na planilha, dia de espera não leva movimento/parado (1034 de 1034 dias em agosto).
        # O banco guarda os tempos do PDF só para conferência.
        if status=='ESPERA FIXA':mov=par=0
        jornada=(seconds(end)-seconds(start)) if start and end else 0
        if jornada<0: jornada+=86400
        # A1=23:59:59, A2=08:00:00, folga vazia (E15=0).
        sem_atividade=(86399-mov-par==86399)
        if sem_atividade: intervalo=descanso=0
        elif jornada==0:
            intervalo=7200
            descanso=43199+intervalo
        else:
            intervalo=jornada-mov-wait
            descanso=86399-jornada+intervalo
        tdte=mov+wait
        if mov<28800 and tdte<28800: extra=0
        elif jornada==0:extra=7200
        else:extra=jornada-intervalo-28800
        he_espera=max(0,wait-28800)
        if min(intervalo,descanso,extra)<0:
            issues.append(f'{date}: fórmula gerou duração negativa; conferir jornada')
        totals['direcao']+=mov;totals['intervalo']+=intervalo;totals['espera']+=wait
        totals['tdte']+=tdte;totals['descanso']+=descanso
        totals['he_espera']+=he_espera;totals['he_total']+=extra
    totals['he_direcao']=totals['he_total']-totals['he_espera']
    return totals,issues

def parse_pdf(path):
    if fitz is None: raise RuntimeError('Biblioteca PyMuPDF não instalada.')
    doc=fitz.open(path)
    records=[]
    for pageno,page in enumerate(doc,1):
        words=page.get_text('words')
        # A data define cada linha; os dados são extraídos pelas posições das colunas do relatório.
        dates=[w for w in words if 350 <= w[0] <= 370 and re.fullmatch(r'\d{2}/\d{2}/\d{4}',w[4])]
        page_name = None
        for w in dates:
            y=(w[1]+w[3])/2
            line=[q for q in words if abs((q[1]+q[3])/2-y)<3.5]
            def at(lo,hi):
                vals=[q[4] for q in sorted(line,key=lambda z:z[0]) if lo<=q[0]<hi]
                return ' '.join(vals).strip()
            plate=at(225,270)
            if not re.fullmatch(r'[A-Z]{3}[0-9][A-Z0-9][0-9]{2}',plate):
                continue
            # O nome aparece na(s) linha(s) à esquerda, imediatamente antes da linha de dados.
            left=[q for q in words if 85<=q[0]<224 and y-15 <= (q[1]+q[3])/2 < y-1]
            if page_name is None:
                first=normalize(' '.join(q[4] for q in sorted(left,key=lambda z:((z[1]+z[3])/2,z[0]))))
                # O cabeçalho do nome pode ocupar duas linhas, acima/abaixo da primeira data.
                continuation=[q for q in words if 85<=q[0]<224 and y+1 <= (q[1]+q[3])/2 <= y+11]
                extra=normalize(' '.join(q[4] for q in sorted(continuation,key=lambda z:(z[1],z[0]))))
                candidate=normalize(first+' '+extra)
                candidate=re.sub(r'\b(?:\(LJ\)|\(MR\)|\(PS\)|OP)\s*\d{4,8}\b','',candidate)
                page_name=normalize(candidate) or 'MOTORISTA NÃO IDENTIFICADO'
            name=page_name
            # Alguns nomes são quebrados em duas linhas, inclusive após a linha da data.
            # Identificador principal usa a placa, mantendo o nome editável para conferência.
            start=at(396,432); end=at(432,468); movement=at(510,560); stopped=at(610,652)
            if not re.fullmatch(r'\d{2}:\d{2}:\d{2}',movement): continue
            if not re.fullmatch(r'\d{2}:\d{2}:\d{2}',stopped): stopped='00:00:00'
            if not re.fullmatch(r'\d{2}:\d{2}:\d{2}',start): start=''
            if not re.fullmatch(r'\d{2}:\d{2}:\d{2}',end): end=''
            wait=seconds(movement)<3600 or not (start and end)
            records.append(dict(data=dt.datetime.strptime(w[4],'%d/%m/%Y').date().isoformat(), placa=plate,
                motorista=name, inicio='' if wait else start, fim='' if wait else end,
                movimento=movement, parado=stopped, espera='10:00:00' if wait else '00:00:00',
                status='ESPERA FIXA' if wait else 'JORNADA', pagina=pageno))
    doc.close()
    if not records: raise ValueError('Nenhuma jornada reconhecida. Verifique o modelo do PDF.')
    return records


# Layout do TXT do Domínio/Contábil 10 Plus: o mesmo do módulo "Planilha de Horas" do HUB RH.
EMPRESA='82';RUBRICA_DIRECAO='150';RUBRICA_ESPERA='571';TIPO_PROCESSO='11'


def plain(name):
    """Nome sem acentos/pontuação, em maiúsculas (normalizar_nome do HUB RH)."""
    name=unicodedata.normalize('NFD',str(name or '').upper().strip())
    name=''.join(c for c in name if unicodedata.category(c)!='Mn')
    return re.sub(r'\s+',' ',re.sub(r'[^A-Z0-9 ]',' ',name)).strip()


def txt_line(code,comp,rubrica,secs):
    """10 + código(10) + AAAAMM + rubrica(9) + 11 + horas HHMM(9) + empresa(10) = 48 dígitos."""
    code=str(code).strip()
    if not code.isdigit() or len(code)>10:raise ValueError(f'Código de funcionário inválido: {code}')
    hhmm=str(secs//3600*100+secs%3600//60)  # 05:30 -> 530; segundos descartados, como no HUB
    line='10'+code.zfill(10)+comp+rubrica.zfill(9)+TIPO_PROCESSO+hhmm.zfill(9)+EMPRESA.zfill(10)
    if len(line)!=48 or not line.isdigit() or not re.fullmatch(r'\d{6}',comp):raise ValueError(f'Linha TXT inválida para o código {code}.')
    return line


def parse_employees(path):
    """Cadastro de funcionários (PDF do Domínio): código, nome, nascimento, admissão, situação, idade."""
    if fitz is None: raise RuntimeError('Biblioteca PyMuPDF não instalada.')
    pat=re.compile(r'(?<!\d)(\d{1,10})\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(\d+)')
    found={}
    with contextlib.closing(fitz.open(path)) as doc:
        for page in doc:
            for block in page.get_text('blocks'):
                if block[3]<70 or 'Sistema licenciado' in block[4]:continue
                for code,name,*_ in pat.findall(block[4]):
                    if name.strip():found[code]={'codigo':code,'nome':name.strip()}
    return list(found.values())


def match_employee(name,employees):
    """Mesmas regras do HUB: exato, provável (>=92%, ou >=80% com folga de 8 pontos) ou duvidoso."""
    exact=[e for e in employees if plain(e['nome'])==plain(name)]
    if len(exact)==1:return 'EXATO',exact[0],1.0
    scores=sorted(((SequenceMatcher(None,plain(name),plain(e['nome'])).ratio(),e) for e in employees),key=lambda x:-x[0])
    if not scores:return 'NAO_ENCONTRADO',None,0.0
    best,emp=scores[0];second=scores[1][0] if len(scores)>1 else 0.0
    return ('PROVAVEL' if best>=0.92 or (best>=0.80 and best-second>=0.08) else 'DUVIDOSO'),emp,best


def payroll(drivers,employees,comp):
    """drivers: [(placa, nome, totais do mês, inconsistências, código salvo)]. Devolve (linhas TXT, lançados, pendências)."""
    by_code={};pend=[];codes={e['codigo']:e for e in employees}
    for plate,name,t,issues,saved in drivers:
        d,e=max(0,t['he_direcao']),max(0,t['he_espera'])
        if issues:pend.append((plate,name,'Dia com duração negativa: conferir antes da folha','','',d,e));continue
        if not d and not e:continue
        if saved:kind,emp,score='CADASTRADO',codes.get(saved,{'codigo':saved,'nome':'(código fora do PDF de cadastro)'}),1.0
        else:kind,emp,score=match_employee(name,employees)
        if kind not in ('CADASTRADO','EXATO','PROVAVEL'):
            pend.append((plate,name,f'Nome divergente ({score:.0%})' if emp else 'Funcionário não encontrado',emp['nome'] if emp else '',emp['codigo'] if emp else '',d,e));continue
        row=by_code.setdefault(emp['codigo'],{'codigo':emp['codigo'],'nome':emp['nome'],'placas':[],'motoristas':[],'direcao':0,'espera':0,'match':'EXATO'})
        if row['motoristas']:pend.append((plate,name,'Mesmo código em mais de uma placa: horas somadas',emp['nome'],emp['codigo'],d,e))
        row['placas'].append(plate);row['motoristas'].append(name);row['direcao']+=d;row['espera']+=e
        if kind!='EXATO':row['match']='CÓDIGO SALVO' if kind=='CADASTRADO' else f'PROVÁVEL {score:.0%}'
    ok=sorted(by_code.values(),key=lambda r:plain(r['nome']))
    lines=[txt_line(r['codigo'],comp,rub,secs) for r in ok for rub,secs in ((RUBRICA_DIRECAO,r['direcao']),(RUBRICA_ESPERA,r['espera'])) if secs>0]
    return lines,ok,pend


def init_db(path):
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.executescript('''CREATE TABLE IF NOT EXISTS jornadas (
        placa TEXT NOT NULL, data TEXT NOT NULL, motorista TEXT NOT NULL,
        inicio TEXT, fim TEXT, movimento TEXT NOT NULL, parado TEXT NOT NULL,
        espera TEXT NOT NULL, status TEXT NOT NULL, fonte TEXT, atualizado TEXT NOT NULL,
        PRIMARY KEY(placa,data));
        CREATE TABLE IF NOT EXISTS auditoria (id INTEGER PRIMARY KEY AUTOINCREMENT,
        instante TEXT, placa TEXT, data TEXT, operacao TEXT, detalhe TEXT);
        CREATE TABLE IF NOT EXISTS motoristas (placa TEXT PRIMARY KEY, nome TEXT NOT NULL, criado TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS importacoes (hash TEXT PRIMARY KEY, arquivo TEXT, instante TEXT, registros INTEGER);''')
        # Código do funcionário no Domínio, confirmado uma vez e usado em todos os TXT seguintes.
        if 'codigo' not in [c[1] for c in con.execute('PRAGMA table_info(motoristas)')]:
            con.execute('ALTER TABLE motoristas ADD COLUMN codigo TEXT');con.commit()


def valid_db(path):
    try:
        with contextlib.closing(sqlite3.connect(path)) as con:
            return con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    except Exception: return False


class App:
    def __init__(self,root):
        self.root=root; self.root.title(APP+' | v0.1.6'); self.root.geometry('1500x850'); self.root.minsize(1150,700); self.root.state('zoomed')
        try:self.root.iconbitmap(default=str(Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'agt_log.ico'))  # vale também para as janelas filhas
        except tk.TclError:pass
        LOCAL.mkdir(parents=True,exist_ok=True)
        self.server=self.load_server()
        self.connected=False
        self.setup_style(); self.build()
        self.root.after(150,self.connect_server)

    def load_config(self):
        try: return json.loads(CONFIG.read_text(encoding='utf8'))
        except Exception: return {}

    def save_config(self,**kw):
        CONFIG.write_text(json.dumps({**self.load_config(),**kw},ensure_ascii=False,indent=2),encoding='utf8')

    def load_server(self):
        return self.load_config().get('servidor',SERVER)

    def setup_style(self):
        self.root.configure(bg=BG)
        st=ttk.Style(); st.theme_use('clam')
        st.configure('.',font=(FONT,10),background=CARD,foreground=INK)
        st.configure('TFrame',background=CARD);st.configure('TLabel',background=CARD,foreground=INK)
        st.configure('TEntry',fieldbackground=BG,bordercolor=LINE,lightcolor=LINE,darkcolor=LINE,padding=6)
        st.map('TEntry',bordercolor=[('focus',INK)],lightcolor=[('focus',INK)])
        st.configure('Treeview',font=(FONT,10),rowheight=34,background=CARD,fieldbackground=CARD,foreground=INK,borderwidth=0)
        st.configure('Mono.Treeview',font=(MONO,10))
        st.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})]);st.layout('Mono.Treeview',st.layout('Treeview'))  # sem moldura escura
        st.configure('Treeview.Heading',font=(FONT,9),background=CARD,foreground=MUTED,relief='flat',borderwidth=0,padding=(8,8))
        st.map('Treeview.Heading',background=[('active',BG)])
        st.map('Treeview',background=[('selected',INK)],foreground=[('selected','#FFFFFF')])
        st.configure('TNotebook',background=CARD,borderwidth=0,bordercolor=CARD,lightcolor=CARD,darkcolor=CARD,tabmargins=(16,0,16,8))
        st.configure('TNotebook.Tab',font=(FONT,10),padding=(14,6),background=CARD,foreground=MUTED,borderwidth=0,lightcolor=CARD,darkcolor=CARD,bordercolor=CARD,focuscolor=CARD)
        st.map('TNotebook.Tab',background=[('selected',BG),('active',BG)],foreground=[('selected',INK),('active',INK)],lightcolor=[('selected',BG)],expand=[('selected',(0,0,0,0))])
        for o in ('Vertical','Horizontal'):
            st.configure(o+'.TScrollbar',background='#D6D6D6',troughcolor=CARD,bordercolor=CARD,arrowcolor=MUTED,lightcolor='#D6D6D6',darkcolor='#D6D6D6',gripcount=0)

    def build(self):
        self.selected_plate = None
        lbl=lambda parent,text,fg=INK,size=10,bg=CARD,**kw:tk.Label(parent,text=text,bg=bg,fg=fg,font=kw.pop('font',(FONT,size)),**kw)
        head=tk.Frame(self.root,bg=CARD,height=64);head.pack(fill='x');head.pack_propagate(False)
        tk.Frame(self.root,bg=LINE,height=1).pack(fill='x')
        try:  # no .exe o PyInstaller extrai os arquivos em sys._MEIPASS
            self.logo=tk.PhotoImage(file=str(Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'logo_agtlog.png'))
            tk.Label(head,image=self.logo,bg=CARD).pack(side='left',padx=(24,4))
        except tk.TclError:lbl(head,'AGT LOG',font=(FONT,12,'bold')).pack(side='left',padx=(24,0))
        lbl(head,'Controle de jornada',MUTED,11).pack(side='left',padx=(8,12))
        nav=tk.Frame(head,bg=CARD);nav.pack(side='left',padx=(8,0));self.page_tabs={}
        for key,text in (('motoristas','Motoristas'),('planilha','Planilha do mês')):
            t=lbl(nav,text,MUTED,font=(FONT+' Semibold',10),padx=14,pady=6,cursor='hand2');t.pack(side='left',padx=(0,4));self.page_tabs[key]=t
            t.bind('<Button-1>',lambda e,k=key:self.show_page(k))
            t.bind('<Enter>',lambda e,t=t:(fade(t,dict(bg=BG),140),t.config(fg=INK)))
            t.bind('<Leave>',lambda e,t=t,k=key:(fade(t,dict(bg=BG if self.page==k else CARD),200),t.config(fg=INK if self.page==k else MUTED)))
        actions=tk.Frame(head,bg=CARD);actions.pack(side='right',padx=24)
        for text,fn,primary in [('↑  Importar PDF',self.import_pdf,True),('+  Cadastrar motorista',self.add_driver,False),('↓  Exportar CSV',self.export_csv,False),('Servidor  →',self.choose_server,False)]:
            live_button(actions,text,fn,primary).pack(side='left',padx=(8,0))
        foot=tk.Frame(self.root,bg=BG);foot.pack(side='bottom',fill='x',padx=24,pady=(0,10))
        self.network=lbl(foot,'● Aguardando servidor',WARN,font=(FONT,9,'bold'),padx=10,pady=3);self.network.pack(side='left',padx=(0,12))
        lbl(foot,'Duplo clique em um dia para conferir ou editar. Fechamento financeiro em validação.',MUTED,9,bg=BG).pack(side='left')
        lbl(foot,'Desenvolvido por Luiz Tyrone  ·  v0.1.6',MUTED,9,bg=BG).pack(side='right')
        content=tk.Frame(self.root,bg=BG)

        side=tk.Frame(content,bg=CARD,width=440,highlightthickness=1,highlightbackground=LINE);side.pack(side='left',fill='y',padx=(0,16));side.pack_propagate(False)
        lbl(side,'Motoristas',font=(FONT,12,'bold')).pack(anchor='w',padx=20,pady=(18,0))
        self.driver_count=lbl(side,'0 motoristas cadastrados',MUTED,9);self.driver_count.pack(anchor='w',padx=20)
        box=tk.Frame(side,bg=BG,highlightthickness=1,highlightbackground=LINE);box.pack(fill='x',padx=20,pady=(14,12))
        lbl(box,'⌕',MUTED,13,bg=BG).pack(side='left',padx=(10,0))
        self.search=tk.StringVar();entry=tk.Entry(box,textvariable=self.search,font=(FONT,10),relief='flat',bg=BG,fg=INK,insertbackground=INK)
        entry.pack(side='left',fill='x',expand=True,ipady=8,padx=8);entry.bind('<KeyRelease>',lambda e:self.refresh_drivers())
        entry.bind('<FocusIn>',lambda e:box.config(highlightbackground=INK));entry.bind('<FocusOut>',lambda e:box.config(highlightbackground=LINE))
        listbox=tk.Frame(side,bg=CARD);listbox.pack(fill='both',expand=True,padx=(12,8),pady=(0,12))
        self.driver_tree=ttk.Treeview(listbox,columns=('nome','tipo','placa'),show='headings',selectmode='browse')
        self.driver_tree.heading('nome',text='Motorista',anchor='w');self.driver_tree.heading('tipo',text='Tipo',anchor='w');self.driver_tree.heading('placa',text='Placa')
        self.driver_tree.column('nome',width=230,minwidth=160,anchor='w');self.driver_tree.column('tipo',width=80,minwidth=70,anchor='w');self.driver_tree.column('placa',width=80,minwidth=75,anchor='center')
        sc=ttk.Scrollbar(listbox,orient='vertical',command=self.driver_tree.yview);self.driver_tree.configure(yscrollcommand=sc.set)
        self.driver_tree.pack(side='left',fill='both',expand=True);sc.pack(side='right',fill='y')
        self.driver_tree.bind('<<TreeviewSelect>>',self.on_driver_select);hover_rows(self.driver_tree)

        right=tk.Frame(content,bg=BG);right.pack(side='left',fill='both',expand=True)
        top=tk.Frame(right,bg=CARD,highlightthickness=1,highlightbackground=LINE);top.pack(fill='x',pady=(0,16))
        row=tk.Frame(top,bg=CARD);row.pack(fill='x',padx=24,pady=(18,16))
        who=tk.Frame(row,bg=CARD);who.pack(side='left')
        self.driver_title=lbl(who,'Selecione um motorista',font=(FONT,18));self.driver_title.pack(anchor='w')
        self.driver_sub=lbl(who,'Escolha um motorista na lista à esquerda para visualizar o controle diário.',MUTED);self.driver_sub.pack(anchor='w')
        self.summary=lbl(who,'',MUTED,9);self.summary.pack(anchor='w')
        filt=tk.Frame(row,bg=CARD);filt.pack(side='right')
        self.month=tk.StringVar(value=dt.date.today().strftime('%Y-%m'));self.month_buttons=[]
        self.month.trace_add('write',lambda *a:[b.set_text(self.month_text()) for b in self.month_buttons])
        self.month_button(filt).pack(side='left',padx=(0,8))
        live_button(filt,'↻  Atualizar',self.refresh).pack(side='left')
        tk.Frame(top,bg=LINE,height=1).pack(fill='x')
        stats=tk.Frame(top,bg=CARD);stats.pack(fill='x')
        self.metric_labels=[]
        for i,(key,label) in enumerate([('direcao','Tempo de direção'),('espera','Tempo de espera'),('he_direcao','HE em direção'),('he_total','Total de hora extra')]):
            if i:tk.Frame(stats,bg=LINE,width=1).grid(row=0,column=2*i-1,sticky='ns')
            cell=tk.Frame(stats,bg=CARD,padx=24,pady=12);cell.grid(row=0,column=2*i,sticky='ew');stats.grid_columnconfigure(2*i,weight=1,uniform='s')
            val=lbl(cell,'—',font=(MONO,16));val.pack(anchor='w');lbl(cell,label,MUTED,9).pack(anchor='w')
            self.metric_labels.append((key,val))

        card=tk.Frame(right,bg=CARD,highlightthickness=1,highlightbackground=LINE);card.pack(fill='both',expand=True)
        tabs=ttk.Notebook(card);tabs.pack(fill='both',expand=True,pady=(10,0))
        daily_tab=tk.Frame(tabs,bg=CARD)
        monthly_tab=tk.Frame(tabs,bg=CARD)
        tabs.add(daily_tab,text='Jornada diária')
        tabs.add(monthly_tab,text='Resumo do mês')
        self.tabs=tabs
        panel=tk.Frame(daily_tab,bg=CARD);panel.pack(fill='both',expand=True)

        cols=('data','inicio','fim','movimento','parado','espera','status')
        self.tree=ttk.Treeview(panel,columns=cols,show='headings',selectmode='browse',style='Mono.Treeview')
        headers=['Data','Início','Fim','Em movimento','Parado ligado','T. espera','Situação']
        widths=[100,90,90,125,125,100,125]
        for c,h,width in zip(cols,headers,widths):self.tree.heading(c,text=h);self.tree.column(c,width=width,minwidth=75,anchor='center')
        vs=ttk.Scrollbar(panel,orient='vertical',command=self.tree.yview);hs=ttk.Scrollbar(panel,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        self.tree.grid(row=0,column=0,sticky='nsew');vs.grid(row=0,column=1,sticky='ns');hs.grid(row=1,column=0,sticky='ew');panel.grid_rowconfigure(0,weight=1);panel.grid_columnconfigure(0,weight=1)
        self.tree.bind('<Double-1>',self.edit_record);hover_rows(self.tree)
        self.tree.tag_configure('espera',background='#FFF4D1')
        bottom=tk.Frame(monthly_tab,bg=CARD);bottom.pack(fill='both',expand=True,padx=18,pady=18)
        metric_names=RESUMO
        metrics=tk.Frame(bottom,bg=CARD);metrics.pack(fill='x')
        for i,(key,label) in enumerate(metric_names):
            total=key=='he_total';bg=INK if total else BG
            cell=tk.Frame(metrics,bg=bg,padx=20,pady=18);cell.grid(row=i//4,column=i%4,sticky='ew',padx=6,pady=6)
            lbl(cell,label,'#D9CCDF' if total else MUTED,9,bg=bg).pack(anchor='w')
            val=lbl(cell,'—',MINT if total else INK,bg=bg,font=(MONO,16));val.pack(anchor='w',pady=(6,0))
            self.metric_labels.append((key,val))
        for col in range(4):metrics.grid_columnconfigure(col,weight=1,uniform='m')
        self.metric_warning=lbl(bottom,'Cálculos baseados nas fórmulas do Excel; validar antes da folha.',MUTED,9)
        self.metric_warning.pack(anchor='w',padx=6,pady=(8,0))
        self.build_sheet(lbl,content);self.show_page('motoristas')

    def build_sheet(self,lbl,content):
        """Segunda página: todos os motoristas lado a lado, como a aba RELATÓRIO da planilha."""
        st=ttk.Style()
        for name,font in (('Sheet.Treeview',(MONO,10)),('SheetL.Treeview',(FONT,9))):
            st.configure(name,font=font,rowheight=26);st.layout(name,st.layout('Treeview'))
        sheet=tk.Frame(self.root,bg=BG);self.pages={'motoristas':content,'planilha':sheet}
        top=tk.Frame(sheet,bg=CARD,highlightthickness=1,highlightbackground=LINE);top.pack(fill='x',pady=(0,16))
        row=tk.Frame(top,bg=CARD);row.pack(fill='x',padx=24,pady=18)
        who=tk.Frame(row,bg=CARD);who.pack(side='left')
        lbl(who,'Planilha de controle de jornada',font=(FONT,18)).pack(anchor='w')
        self.sheet_sub=lbl(who,'Todos os motoristas do mês lado a lado.',MUTED);self.sheet_sub.pack(anchor='w')
        filt=tk.Frame(row,bg=CARD);filt.pack(side='right')
        self.month_button(filt).pack(side='left',padx=(0,8));live_button(filt,'↻  Atualizar',self.refresh).pack(side='left')
        live_button(filt,'↓  Gerar TXT contábil',self.export_txt,True).pack(side='left',padx=(8,0))
        body=tk.Frame(sheet,bg=CARD,highlightthickness=1,highlightbackground=LINE);body.pack(fill='both',expand=True)
        self.sheet_left=ttk.Treeview(body,columns=('dia','item'),show='headings',selectmode='none',style='SheetL.Treeview')
        self.sheet_left.heading('dia',text='Dia');self.sheet_left.heading('item',text='Item',anchor='w')
        self.sheet_left.column('dia',width=50,stretch=False,anchor='center');self.sheet_left.column('item',width=tkfont.Font(family=FONT,size=9).measure('Hora extra total')+28,stretch=False,anchor='w')
        self.sheet=ttk.Treeview(body,show='headings',selectmode='none',style='Sheet.Treeview')
        # Só a grade principal rola; a coluna Dia/Item acompanha (fica fixa na horizontal).
        vs=ttk.Scrollbar(body,orient='vertical',command=lambda *a:(self.sheet.yview(*a),self.sheet_left.yview(*a)))
        # O Treeview redesenha TODAS as colunas a cada rolagem (inclusive fora da tela); por isso só as visíveis ficam em displaycolumns.
        self.sheet_hs=ttk.Scrollbar(body,orient='horizontal',command=self.sheet_xscroll);self.sheet_cols=[];self.sheet_x=0;self.sheet_colw=1
        self.sheet.configure(yscrollcommand=lambda f,l:(vs.set(f,l),self.sheet_left.yview_moveto(f)))
        self.sheet.bind('<Configure>',lambda e:self.sheet_view())
        self.sheet_left.bind('<MouseWheel>',lambda e:(self.sheet.yview_scroll(-e.delta//120,'units'),'break')[1])
        self.sheet.bind('<Shift-MouseWheel>',lambda e:self.sheet_xscroll('scroll',-e.delta//120,'units'))
        for t in (self.sheet_left,self.sheet):
            t.tag_configure('par',background='#F8F7F9');t.tag_configure('total',background='#FFF4D1')
        self.sheet_left.grid(row=0,column=0,sticky='ns');tk.Frame(body,bg=LINE,width=1).grid(row=0,column=1,sticky='ns')
        self.sheet.grid(row=0,column=2,sticky='nsew');vs.grid(row=0,column=3,sticky='ns');self.sheet_hs.grid(row=1,column=2,sticky='ew')
        body.grid_rowconfigure(0,weight=1);body.grid_columnconfigure(2,weight=1)

    def sheet_view(self):
        """Mostra só as colunas de motoristas que cabem na tela, a partir de self.sheet_x."""
        n=len(self.sheet_cols);k=max(1,self.sheet.winfo_width()//self.sheet_colw+1)
        self.sheet_x=max(0,min(self.sheet_x,n-k+1));cols=self.sheet_cols[self.sheet_x:self.sheet_x+k]
        self.sheet.configure(displaycolumns=cols or '#all')
        self.sheet_hs.set(*((self.sheet_x/n,min(1,(self.sheet_x+k-1)/n)) if n else (0,1)))

    def sheet_xscroll(self,*a):
        k=max(1,self.sheet.winfo_width()//self.sheet_colw)
        if a[0]=='moveto':self.sheet_x=round(float(a[1])*len(self.sheet_cols))
        else:self.sheet_x+=int(a[1])*(k if a[2]=='pages' else 1)
        self.sheet_view()

    def show_page(self,key):
        self.page=key
        for p in self.pages.values():p.pack_forget()
        self.pages[key].pack(fill='both',expand=True,padx=24,pady=16)
        for k,t in self.page_tabs.items():t.config(bg=BG if k==key else CARD,fg=INK if k==key else MUTED)
        if key=='planilha':self.refresh_sheet()

    def refresh_sheet(self):
        for t in (self.sheet_left,self.sheet):t.delete(*t.get_children())
        if not self.connected:return
        month=self.month.get();y,m=map(int,month.split('-'))
        with sqlite3.connect(DB) as con:
            rows=con.execute('SELECT placa,data,inicio,fim,movimento,parado,espera,status FROM jornadas WHERE data LIKE ? ORDER BY data',(month+'%',)).fetchall()
            names=dict(con.execute('SELECT placa,nome FROM motoristas'))
        by={}
        for r in rows:by.setdefault(r[0],{})[r[1]]=r[1:]
        name=lambda p:driver_parts(names.get(p,p))[0]
        plates=sorted(by,key=name)
        self.sheet.configure(columns=plates,displaycolumns='#all');self.sheet_cols=plates;self.sheet_x=0
        width=tkfont.Font(family=MONO,size=10).measure('00:00:00 – 00:00:00')+24  # largura acompanha a escala do Windows
        for p in plates:self.sheet.heading(p,text=f'{name(p)}  ·  {p}');self.sheet.column(p,width=width,minwidth=width,stretch=False,anchor='center')
        self.sheet_colw=width;self.sheet_view()
        fmt=lambda v:hhmmss(v) if v>=0 else 'REVISAR'
        # r = (data,inicio,fim,movimento,parado,espera,status); t = month_metrics de um único dia.
        items=[('Período jornada',lambda r,t:f'{r[1]} – {r[2]}' if r[1] and r[2] else 'Espera fixa' if r[6]=='ESPERA FIXA' else ''),
               ('Em movimento',lambda r,t:fmt(t['direcao'])),
               ('Parado ligado',lambda r,t:'00:00:00' if r[6]=='ESPERA FIXA' else r[4]),
               ('T. espera',lambda r,t:fmt(t['espera'])),('HE em T espera',lambda r,t:fmt(t['he_espera'])),('TE + TD',lambda r,t:fmt(t['tdte'])),
               ('Intervalo',lambda r,t:fmt(t['intervalo'])),('Descanso',lambda r,t:fmt(t['descanso'])),
               ('Hora extra total',lambda r,t:fmt(t['he_total']))]
        for d in range(1,calendar.monthrange(y,m)[1]+1):
            date=f'{month}-{d:02d}';tag=('par',) if d%2==0 else ()
            cells={p:(by[p][date],month_metrics([by[p][date]])[0]) for p in plates if date in by[p]}
            for i,(label,get) in enumerate(items):
                self.sheet_left.insert('','end',values=(f'{d:02d}' if i==0 else '',label),tags=tag)
                self.sheet.insert('','end',values=[get(*cells[p]) if p in cells else '' for p in plates],tags=tag)
        totals={p:month_metrics(list(by[p].values()))[0] for p in plates}
        for i,(key,label) in enumerate(RESUMO):
            self.sheet_left.insert('','end',values=('Mês' if i==0 else '',label),tags=('total',))
            self.sheet.insert('','end',values=[fmt(totals[p][key]) for p in plates],tags=('total',))
        self.sheet_sub.config(text=f'{MESES[m-1]} de {y}  ·  {len(plates)} motoristas  ·  {len(rows)} dias lançados')

    def month_text(self):
        y,m=self.month.get().split('-')
        return f'{MESES[int(m)-1]} de {y}   ▾'

    def month_button(self,parent):
        b=live_button(parent,self.month_text(),self.pick_month);self.month_buttons.append(b);return b

    def pick_month(self):
        """Escolha da competência: primeiro o ano, depois o mês."""
        cur_y,cur_m=map(int,self.month.get().split('-'))
        years={dt.date.today().year,cur_y};filled=set()
        if DB.exists():
            try:
                with contextlib.closing(sqlite3.connect(DB)) as con:
                    filled={tuple(map(int,ym.split('-'))) for (ym,) in con.execute('SELECT DISTINCT substr(data,1,7) FROM jornadas')}
                years|={y for y,_ in filled}
            except sqlite3.Error:pass
        win=tk.Toplevel(self.root);win.title('Competência');win.configure(bg=CARD);win.resizable(False,False);win.transient(self.root);win.grab_set()
        body=tk.Frame(win,bg=CARD,padx=24,pady=20);body.pack(fill='both',expand=True)
        def screen(title,back=None):
            for w in body.winfo_children():w.destroy()
            head=tk.Frame(body,bg=CARD);head.pack(fill='x',pady=(0,16))
            if back:live_button(head,'←  Anos',back).pack(side='left',padx=(0,12))
            tk.Label(head,text=title,bg=CARD,fg=INK,font=(FONT,15)).pack(side='left')
            grid=tk.Frame(body,bg=CARD);grid.pack(fill='x');return grid
        def years_screen():
            grid=screen('Escolha o ano')
            for i,y in enumerate(sorted(years)):
                live_button(grid,str(y),lambda y=y:months_screen(y),y==cur_y).grid(row=i//3,column=i%3,padx=4,pady=4,sticky='ew')
            for c in range(3):grid.grid_columnconfigure(c,weight=1,uniform='y',minsize=110)
        def months_screen(y):
            grid=screen(f'{y}  ·  escolha o mês',years_screen)
            for i,name in enumerate(MESES):
                b=live_button(grid,name,lambda m=i+1:choose(y,m),(y,i+1)==(cur_y,cur_m))
                b.grid(row=i//4,column=i%4,padx=4,pady=4,sticky='ew')
                if (y,i+1) not in filled and (y,i+1)!=(cur_y,cur_m):b.set_fg(MUTED)  # mês sem jornadas
            for c in range(4):grid.grid_columnconfigure(c,weight=1,uniform='m',minsize=110)
        def choose(y,m):
            self.month.set(f'{y}-{m:02d}');win.destroy();self.refresh()
        years_screen()

    def sync_drivers(self):
        with sqlite3.connect(DB) as con:
            con.execute('INSERT OR IGNORE INTO motoristas(placa,nome,criado) SELECT placa,motorista,MIN(atualizado) FROM jornadas GROUP BY placa')

    def add_driver(self):
        if not self.connected:return messagebox.showwarning('Servidor','Conecte ao servidor primeiro.')
        win=tk.Toplevel(self.root);win.title('Cadastrar motorista');win.geometry('430x290');win.transient(self.root);win.grab_set()
        frm=ttk.Frame(win,padding=22);frm.pack(fill='both',expand=True)
        ttk.Label(frm,text='Novo motorista',font=('Segoe UI',15,'bold')).pack(anchor='w',pady=(0,15))
        ttk.Label(frm,text='Nome completo').pack(anchor='w');name=tk.StringVar();ttk.Entry(frm,textvariable=name).pack(fill='x',pady=(3,12))
        ttk.Label(frm,text='Placa do veículo (identificador do cadastro)').pack(anchor='w');plate=tk.StringVar();ttk.Entry(frm,textvariable=plate).pack(fill='x',pady=(3,12))
        def save():
            n=normalize(name.get());pl=plate.get().strip().upper()
            if not n or not re.fullmatch(r'[A-Z]{3}[0-9][A-Z0-9][0-9]{2}',pl):return messagebox.showerror('Cadastro','Informe o nome e uma placa válida.',parent=win)
            try:
                with sqlite3.connect(DB) as con:
                    con.execute('INSERT INTO motoristas(placa,nome,criado) VALUES(?,?,?) ON CONFLICT(placa) DO UPDATE SET nome=excluded.nome',(pl,n,dt.datetime.now().isoformat()))
                self.publish();self.selected_plate=pl;win.destroy();self.refresh()
            except Exception as exc:messagebox.showerror('Cadastro',str(exc),parent=win)
        live_button(frm,'Salvar cadastro',save,True).pack(fill='x',pady=10)

    def on_driver_select(self,event=None):
        selection=self.driver_tree.selection()
        if not selection:return
        self.selected_plate=selection[0]
        self.refresh_days()

    def refresh_drivers(self):
        if not self.connected:return
        previous=self.selected_plate
        self.driver_tree.delete(*self.driver_tree.get_children())
        with sqlite3.connect(DB) as con:
            drivers=con.execute('SELECT placa,nome FROM motoristas ORDER BY nome').fetchall()
        term=self.search.get().strip().upper();visible=0
        for pl,n in drivers:
            clean,kind=driver_parts(n)
            if term and term not in (n+' '+pl).upper():continue
            self.driver_tree.insert('', 'end',iid=pl,values=(clean,kind,pl));visible+=1
        self.driver_count.config(text=f'{visible} motoristas cadastrados')
        if previous and self.driver_tree.exists(previous):
            self.driver_tree.selection_set(previous);self.driver_tree.see(previous)
        elif visible and not previous:
            first=self.driver_tree.get_children()[0];self.selected_plate=first;self.driver_tree.selection_set(first)
        elif previous and not self.driver_tree.exists(previous):self.selected_plate=None

    def refresh_days(self):
        self.tree.delete(*self.tree.get_children())
        if not self.connected or not self.selected_plate:
            self.driver_title.config(text='Selecione um motorista');self.driver_sub.config(text='Escolha um cadastro na lista à esquerda.');self.summary.config(text='');
            for _,label in self.metric_labels:label.config(text='—')
            return
        with sqlite3.connect(DB) as con:
            driver=con.execute('SELECT nome FROM motoristas WHERE placa=?',(self.selected_plate,)).fetchone()
            rows=con.execute('SELECT data,inicio,fim,movimento,parado,espera,status FROM jornadas WHERE placa=? AND data LIKE ? ORDER BY data',(self.selected_plate,self.month.get().strip()+'%',)).fetchall()
        clean,kind=driver_parts(driver[0] if driver else 'Motorista')
        self.driver_title.config(text=clean)
        self.driver_sub.config(text=f'{kind}  •  Placa {self.selected_plate}  •  Histórico diário de jornada')
        for r in rows:self.tree.insert('', 'end',values=(dt.date.fromisoformat(r[0]).strftime('%d/%m/%Y'),)+r[1:],tags=('espera',) if r[-1]=='ESPERA FIXA' else ())
        self.summary.config(text=f'{len(rows)} dias  |  {sum(r[-1]=="ESPERA FIXA" for r in rows)} esperas fixas')
        totals,issues=month_metrics(rows)
        for key,label in self.metric_labels:label.config(text=hhmmss(totals[key]) if totals[key]>=0 else 'REVISAR')
        self.metric_warning.config(text=('ATENÇÃO: '+str(len(issues))+' dia(s) com inconsistência; não usar para folha.' if issues else 'Cálculos conforme fórmulas do Excel; validar antes do fechamento de folha.'),fg=ERR if issues else MUTED)

    def connect_server(self):
        path=Path(self.server)
        try:
            if not path.is_dir(): raise FileNotFoundError('A pasta não está acessível.')
            test=path / ('.teste_acesso_'+str(os.getpid()))
            test.write_text('ok');test.unlink()
            (path/'Backups').mkdir(exist_ok=True);(path/'PDFs Originais').mkdir(exist_ok=True);(path/'Relatorios').mkdir(exist_ok=True)
            if PENDING.exists() and DB.exists() and valid_db(DB):
                # Último envio falhou: o local é o mais novo, enviar antes de baixar.
                self.publish()
            remote=self.latest_remote(path)
            if remote is not None:
                if not valid_db(remote): raise RuntimeError('Banco no servidor não passou na verificação de integridade.')
                shutil.copy2(remote,DB)
            else:
                if DB.exists():
                    # Nunca sobrescrever um servidor vazio com cópia local sem aprovação.
                    if not messagebox.askyesno('Banco encontrado no computador','O servidor não possui banco, mas há uma cópia local. Publicar essa cópia no servidor?'): return
                else: init_db(DB)
                self.publish(initial=True)
            init_db(DB);self.sync_drivers();self.connected=True
            self.network.config(text='● Servidor conectado',fg=OK)
            self.save_config(servidor=self.server)
            self.refresh()
        except Exception as e:
            self.connected=False;self.network.config(text='● Servidor indisponível',fg=ERR)
            if messagebox.askyesno('Pasta de dados não encontrada',f'Não foi possível acessar a pasta compartilhada:\n{self.server}\n\nDetalhe: {e}\n\nDeseja localizar a pasta manualmente?'):
                self.choose_server()

    def choose_server(self):
        folder=filedialog.askdirectory(title='Selecione a pasta DADOS DO NOVO SISTEMA')
        if folder:self.server=folder;self.connect_server()

    def latest_remote(self,path):
        # Cada salvamento cria uma versão própria: nunca substituímos um arquivo aberto no SMB.
        versions=sorted(path.glob('jornadas_*.db'),reverse=True)
        legacy=path/'jornadas.db'
        if legacy.is_file(): versions.append(legacy)
        if not versions:return None
        for candidate in versions:
            if valid_db(candidate):return candidate
        raise RuntimeError('Existem bancos no servidor, mas nenhum passou na verificação de integridade.')

    def publish(self,initial=False):
        path=Path(self.server)
        PENDING.write_text(dt.datetime.now().isoformat(),encoding='utf8')
        # API de backup do SQLite garante cópia consistente, mesmo com conexão local aberta.
        # close() explícito: no Windows, conexão aberta trava o arquivo e impede apagar o snapshot.
        src=sqlite3.connect(DB,timeout=15)
        try:
            src.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            stamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            snapshot=LOCAL/('snapshot_'+stamp+'.db')
            dst=sqlite3.connect(snapshot)
            try:src.backup(dst)
            finally:dst.close()
        finally:src.close()
        try:
            if not valid_db(snapshot):raise RuntimeError('O banco local não passou na verificação de integridade.')
            # Nome exclusivo: não usa os.replace() no compartilhamento de rede.
            remote=path/('jornadas_'+stamp+'.db')
            try:
                with open(snapshot,'rb') as inp,open(remote,'xb') as out:
                    shutil.copyfileobj(inp,out)
                    out.flush();os.fsync(out.fileno())
                if not valid_db(remote):raise RuntimeError('Cópia enviada ao servidor inválida.')
            except Exception:
                # Não apagar cópia incompleta: pode estar bloqueada por antivírus ou SMB.
                self.network.config(text='● Alterações pendentes de envio',fg=ERR)
                raise
            PENDING.unlink(missing_ok=True)
            self.network.config(text='● Servidor conectado',fg=OK)
            self.prune_versions(path)
        finally:
            snapshot.unlink(missing_ok=True)

    def prune_versions(self,path):
        """Mantém as KEEP_VERSIONS mais recentes; das antigas guarda a última de cada dia em Backups."""
        versions=sorted(path.glob('jornadas_*.db'),reverse=True)[KEEP_VERSIONS:]
        backups=path/'Backups';kept_days=set()
        for old in versions:  # da mais nova para a mais antiga
            day=old.name[9:17]
            try:
                if day in kept_days:old.unlink();continue
                kept_days.add(day)
                shutil.move(str(old),backups/old.name)
                for prev in backups.glob(f'jornadas_{day}_*.db'):
                    if prev.name!=old.name:prev.unlink()
            except OSError:pass  # arquivo bloqueado no SMB: tenta de novo no próximo envio

    def refresh(self):
        if not self.connected:return
        self.refresh_drivers();self.refresh_days()
        if self.page=='planilha':self.refresh_sheet()

    def import_pdf(self):
        if not self.connected:messagebox.showwarning('Servidor','Conecte ao servidor antes de importar.');return
        files=filedialog.askopenfilenames(title='Selecionar PDF(s) do rastreador',filetypes=[('Arquivos PDF','*.pdf')])
        if not files:return
        total=0;errors=[]
        for file in files:
            try:
                data=Path(file).read_bytes();digest=hashlib.sha256(data).hexdigest()
                with sqlite3.connect(DB) as con:
                    if con.execute('SELECT 1 FROM importacoes WHERE hash=?',(digest,)).fetchone():continue
                records=parse_pdf(file)
                with sqlite3.connect(DB) as con:
                    for r in records:
                        old=con.execute('SELECT inicio,fim,movimento,parado,espera,status FROM jornadas WHERE placa=? AND data=?',(r['placa'],r['data'])).fetchone()
                        if old and old==tuple(r[k] for k in ('inicio','fim','movimento','parado','espera','status')):continue
                        # Não sobrescrever correções manuais automaticamente.
                        if old and con.execute("SELECT 1 FROM auditoria WHERE placa=? AND data=? AND operacao='EDICAO MANUAL' LIMIT 1",(r['placa'],r['data'])).fetchone():continue
                        con.execute('''INSERT INTO jornadas(placa,data,motorista,inicio,fim,movimento,parado,espera,status,fonte,atualizado)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(placa,data) DO UPDATE SET
                        motorista=excluded.motorista,inicio=excluded.inicio,fim=excluded.fim,movimento=excluded.movimento,
                        parado=excluded.parado,espera=excluded.espera,status=excluded.status,fonte=excluded.fonte,atualizado=excluded.atualizado''',
                        (r['placa'],r['data'],r['motorista'],r['inicio'],r['fim'],r['movimento'],r['parado'],r['espera'],r['status'],digest,dt.datetime.now().isoformat()))
                        con.execute('INSERT INTO auditoria(instante,placa,data,operacao,detalhe) VALUES(?,?,?,?,?)',
                                    (dt.datetime.now().isoformat(),r['placa'],r['data'],'IMPORTAÇÃO',Path(file).name))
                        total+=1
                    con.execute('INSERT INTO importacoes VALUES(?,?,?,?)',(digest,Path(file).name,dt.datetime.now().isoformat(),len(records)))
                self.sync_drivers()
                dest=Path(self.server)/'PDFs Originais'/(digest[:12]+'_'+Path(file).name)
                if not dest.exists():shutil.copy2(file,dest)
                self.publish()
            except Exception as e:errors.append(f'{Path(file).name}: {e}')
        self.refresh()
        messagebox.showinfo('Importação',f'{total} jornadas inseridas/atualizadas.\n'+('\n'.join(errors) if errors else 'Dados salvos no servidor.'))

    def edit_record(self,event=None):
        selected=self.tree.selection()
        if not selected:return
        row=self.tree.item(selected[0],'values');date=dt.datetime.strptime(row[0],'%d/%m/%Y').date().isoformat();plate=self.selected_plate
        win=tk.Toplevel(self.root);win.title('Conferir jornada | '+self.driver_title.cget('text'));win.geometry('440x470');win.transient(self.root);win.grab_set()
        frm=ttk.Frame(win,padding=18);frm.pack(fill='both',expand=True)
        ttk.Label(frm,text=f'{self.driver_title.cget("text")}  •  {row[0]}',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,12))
        entries={}
        for label,val in [('Hora início',row[1]),('Hora fim',row[2]),('Em movimento',row[3]),('Parado ligado',row[4])]:
            ttk.Label(frm,text=label).pack(anchor='w');var=tk.StringVar(value=val);ttk.Entry(frm,textvariable=var).pack(fill='x',pady=(1,7));entries[label]=var
        ttk.Label(frm,text='Motivo da alteração (obrigatório)').pack(anchor='w');reason=tk.StringVar();ttk.Entry(frm,textvariable=reason).pack(fill='x',pady=5)
        def save():
            try:
                if not reason.get().strip():raise ValueError('Informe o motivo da alteração.')
                start=entries['Hora início'].get().strip();end=entries['Hora fim'].get().strip();mov=entries['Em movimento'].get().strip();stop=entries['Parado ligado'].get().strip()
                for t in (start,end,mov,stop):
                    if t and not re.fullmatch(r'\d{2}:\d{2}:\d{2}',t):raise ValueError('Use o formato HH:MM:SS.')
                if not mov or not stop:raise ValueError('Movimento e parado ligado são obrigatórios.')
                wait=seconds(mov)<3600 or not(start and end)
                with sqlite3.connect(DB) as con:
                    con.execute('UPDATE jornadas SET inicio=?,fim=?,movimento=?,parado=?,espera=?,status=?,atualizado=? WHERE placa=? AND data=?',
                                ('' if wait else start,'' if wait else end,mov,stop,'10:00:00' if wait else '00:00:00','ESPERA FIXA' if wait else 'JORNADA',dt.datetime.now().isoformat(),plate,date))
                    con.execute('INSERT INTO auditoria(instante,placa,data,operacao,detalhe) VALUES(?,?,?,?,?)',
                                (dt.datetime.now().isoformat(),plate,date,'EDICAO MANUAL',reason.get().strip()))
                self.publish();self.refresh();win.destroy()
            except Exception as e:messagebox.showerror('Não foi possível salvar',str(e),parent=win)
        live_button(frm,'Salvar alteração no servidor',save,True).pack(fill='x',pady=14)

    def export_csv(self):
        if not self.connected:return
        dest=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='jornadas_'+self.month.get()+'.csv')
        if not dest:return
        if not self.selected_plate:return messagebox.showwarning('Exportação','Selecione um motorista primeiro.')
        with sqlite3.connect(DB) as con:
            rows=con.execute('SELECT data,motorista,placa,inicio,fim,movimento,parado,espera,status FROM jornadas WHERE placa=? AND data LIKE ? ORDER BY data',(self.selected_plate,self.month.get()+'%')).fetchall()
        with open(dest,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f,delimiter=';');w.writerow(['Data','Motorista','Placa','Início','Fim','Em movimento','Parado ligado','T. espera','Regra']);w.writerows(rows)
        messagebox.showinfo('Exportação',f'Arquivo exportado:\n{dest}')

    def export_txt(self,pick=False):
        """Gera o TXT de horas extras para o Domínio, como o módulo Planilha de Horas do HUB RH."""
        if not self.connected:return messagebox.showwarning('Servidor','Conecte ao servidor primeiro.')
        cadastro=self.load_config().get('cadastro_pdf','')
        if pick or not Path(cadastro).is_file():
            cadastro=filedialog.askopenfilename(title='Selecione o PDF de cadastro de funcionários (Domínio)',filetypes=[('Arquivos PDF','*.pdf')])
            if not cadastro:return
        try:
            employees=parse_employees(cadastro)
            if not employees:raise ValueError('Nenhum funcionário encontrado neste PDF. Confira se é o cadastro de funcionários do Domínio.')
        except Exception as e:return messagebox.showerror('Cadastro de funcionários',str(e))
        self.save_config(cadastro_pdf=cadastro)
        month=self.month.get();y,m=month.split('-')
        with sqlite3.connect(DB) as con:
            rows=con.execute('SELECT placa,data,inicio,fim,movimento,parado,espera,status FROM jornadas WHERE data LIKE ? ORDER BY data',(month+'%',)).fetchall()
            cad={pl:(n,c) for pl,n,c in con.execute('SELECT placa,nome,codigo FROM motoristas')}
        by={}
        for r in rows:by.setdefault(r[0],[]).append(r[1:])
        drivers=[(p,driver_parts(cad.get(p,(p,))[0])[0],*month_metrics(days),(cad.get(p,(p,None))[1] or '')) for p,days in by.items()]
        try:lines,ok,pend=payroll(drivers,employees,y+m)
        except ValueError as e:return messagebox.showerror('TXT contábil',str(e))
        win=tk.Toplevel(self.root);win.title(f'TXT contábil | {MESES[int(m)-1]} de {y}');win.geometry(f'{int(win.winfo_screenwidth()*.8)}x{int(win.winfo_screenheight()*.8)}');win.configure(bg=CARD);win.transient(self.root)
        frm=tk.Frame(win,bg=CARD,padx=22,pady=18);frm.pack(fill='both',expand=True)
        tk.Label(frm,text=f'{len(lines)} lançamentos para {len(ok)} funcionários  ·  {len(pend)} pendência(s)',bg=CARD,fg=INK,font=(FONT,14)).pack(anchor='w')
        tk.Label(frm,text=f'Rubricas {RUBRICA_DIRECAO} (HE em direção) e {RUBRICA_ESPERA} (HE em T espera)  ·  empresa {EMPRESA}  ·  cadastro: {Path(cadastro).name}',bg=CARD,fg=MUTED,font=(FONT,9)).pack(anchor='w',pady=(0,10))
        bar=tk.Frame(frm,bg=CARD);bar.pack(side='bottom',fill='x',pady=(12,0))  # rodapé primeiro: nunca é cortado
        tk.Label(frm,text='Duplo clique em uma linha para informar ou corrigir o código do funcionário. O código fica salvo para os próximos meses.',bg=CARD,fg=MUTED,font=(FONT,9)).pack(side='bottom',anchor='w',pady=(8,0))
        tabs=ttk.Notebook(frm);tabs.pack(fill='both',expand=True)
        def set_code(plate,name,suggest):
            # O código fica salvo no cadastro do motorista (servidor): nos próximos meses ele entra sozinho no TXT.
            dlg=tk.Toplevel(win);dlg.title('Código no Domínio');dlg.configure(bg=CARD);dlg.transient(win);dlg.grab_set()
            box=tk.Frame(dlg,bg=CARD,padx=22,pady=18);box.pack(fill='both',expand=True)
            tk.Label(box,text=f'{name}  ·  placa {plate}',bg=CARD,fg=INK,font=(FONT,12)).pack(anchor='w')
            tk.Label(box,text='Código do funcionário no Domínio (deixe vazio para voltar à busca pelo nome):',bg=CARD,fg=MUTED,font=(FONT,9)).pack(anchor='w',pady=(8,4))
            var=tk.StringVar(value=suggest);ent=ttk.Entry(box,textvariable=var,font=(MONO,11));ent.pack(fill='x');ent.focus_set()
            hint=tk.Label(box,text='',bg=CARD,fg=MUTED,font=(FONT,9));hint.pack(anchor='w',pady=(4,0))
            show=lambda *a:hint.config(text=next((e['nome'] for e in employees if e['codigo']==var.get().strip()),'Código não está no PDF de cadastro' if var.get().strip() else ''))
            var.trace_add('write',show);show()
            def ok():
                c=var.get().strip()
                if c and (not c.isdigit() or len(c)>10):return messagebox.showerror('Código','Use só números, até 10 dígitos.',parent=dlg)
                try:
                    with sqlite3.connect(DB) as con:
                        con.execute('INSERT INTO motoristas(placa,nome,criado,codigo) VALUES(?,?,?,?) ON CONFLICT(placa) DO UPDATE SET codigo=excluded.codigo',(plate,name,dt.datetime.now().isoformat(),c or None))
                        con.execute('INSERT INTO auditoria(instante,placa,data,operacao,detalhe) VALUES(?,?,?,?,?)',(dt.datetime.now().isoformat(),plate,'','CODIGO DOMINIO',c or 'removido'))
                    self.publish()
                except Exception as e:return messagebox.showerror('Código',str(e),parent=dlg)
                dlg.destroy();win.destroy();self.export_txt()
            live_button(box,'Salvar código',ok,True).pack(fill='x',pady=(12,0));ent.bind('<Return>',lambda e:ok())
        def table(title,cols,data,keys):
            box=tk.Frame(tabs,bg=CARD);tabs.add(box,text=title)
            t=ttk.Treeview(box,columns=[c for c,_,_ in cols],show='headings')
            t.bind('<Double-1>',lambda e:(lambda i:i and set_code(*keys[int(i)]))(t.focus()))
            for c,h,w in cols:t.heading(c,text=h,anchor='w');t.column(c,width=w,anchor='w')
            sc=ttk.Scrollbar(box,orient='vertical',command=t.yview);t.configure(yscrollcommand=sc.set)
            t.pack(side='left',fill='both',expand=True);sc.pack(side='right',fill='y')
            for i,row in enumerate(data):t.insert('','end',iid=str(i),values=row)
        table(f'Vão para o TXT ({len(ok)})',[('c','Código',80),('n','Funcionário (cadastro)',260),('p','Motorista (sistema)',260),('d','HE direção',100),('e','HE espera',100),('m','Identificação',110)],
              [(r['codigo'],r['nome'],' + '.join(r['motoristas']),hhmmss(r['direcao']),hhmmss(r['espera']),r['match']) for r in ok],
              [(r['placas'][0],r['motoristas'][0],r['codigo']) for r in ok])
        table(f'Pendências ({len(pend)})',[('n','Motorista (sistema)',230),('mo','Motivo',300),('s','Sugestão do cadastro',230),('c','Código',70),('d','HE direção',90),('e','HE espera',90)],
              [(n,mo,s,c,hhmmss(d),hhmmss(e)) for _,n,mo,s,c,d,e in pend],[(pl,n,c) for pl,n,_,_,c,_,_ in pend])
        def save():
            if pend and not messagebox.askyesno('Existem pendências',f'{len(pend)} motorista(s) com pendência NÃO entram no TXT.\nGerar o arquivo só com os lançamentos validados?',icon='warning',parent=win):return
            dest=filedialog.asksaveasfilename(parent=win,title='Salvar arquivo para o Domínio',defaultextension='.txt',filetypes=[('Arquivo TXT','*.txt')],
                                              initialdir=str(Path(self.server)/'Relatorios'),initialfile=f'HE_{m}{y}.txt')
            if not dest:return
            with open(dest,'w',encoding='utf-8',newline='') as f:f.write('\n'.join(lines))
            messagebox.showinfo('TXT gerado',f'Arquivo criado com {len(lines)} lançamento(s).\n\n{dest}',parent=win)
        live_button(bar,'Salvar TXT',save,True).pack(side='right')
        live_button(bar,'Trocar PDF de cadastro',lambda:(win.destroy(),self.export_txt(True))).pack(side='right',padx=8)
        if not lines:messagebox.showwarning('TXT contábil','Nenhum lançamento validado neste mês.',parent=win)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
