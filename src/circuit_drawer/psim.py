"""PSIM Python builder using the vendor API; exporting needs no PSIM runtime.

The native schema and terminal directions were read back with
PsimConvertToPython in PSIM 2026.0. Electrical net labels join the checked
graph, independently of geometric crossings. The source JSON is unchanged.
"""
from copy import deepcopy
import json
import math

from .export_layout import plan
from .simulation_export import Part
from .spice import SpiceError

# PSIM requires a strictly positive switch on resistance. Its numerical
# conductances are explicitly compensated by the exported series/shunt pair.
SWITCH_ON = 1e-9
SWITCH_OFF = 1e12
KINDS = {'R': 'R', 'L': 'L', 'C': 'C', 'D': 'DIODE', 'S': 'SSWI',
         'T': 'TF_IDEAL', 'VM': 'VP2', 'AM': 'IP'}
ANGLES = {'right': 0, 'down': 90, 'left': 180, 'up': 270}


def _rotate(xy, direction):
    x, y = xy
    return {'right': (x,y), 'down': (-y,x), 'left': (-x,-y), 'up': (y,-x)}[direction]


def terminal(part, index, direction):
    points = ((-25,-25),(-25,25),(25,-25),(25,25)) if part.kind == 'T' else (
        ((20,0),(-20,0)) if part.kind in ('AM','VM') else ((25,0),(-25,0)))
    return _rotate(points[index], direction)


def gate_side(part, direction):
    return _rotate((-5,20), direction) if part.kind == 'S' else None


def _switch_resistors(ron, roff):
    """Compensate PSIM's finite numerical switch resistance, on and off."""
    if not SWITCH_ON < ron < roff < SWITCH_OFF:
        raise SpiceError(f'PSIM 开关要求 {SWITCH_ON:g} < Ron < Roff < {SWITCH_OFF:g} Ω；请核对器件参数')
    conductance = 1/ron - 1/roff
    c = (SWITCH_OFF-SWITCH_ON)/conductance - SWITCH_ON*SWITCH_OFF
    b = SWITCH_ON + SWITCH_OFF
    series = 2*c / (math.sqrt(b*b+4*c)+b)
    shunt = 1/(1/roff - 1/(SWITCH_OFF+series))
    if not all(math.isfinite(v) and v > 0 for v in (series,shunt)):
        raise SpiceError('PSIM 开关等效电阻超出数值范围')
    return series, shunt


def _edges(points, stop):
    """The same 0.5 gate threshold as the other ideal-switch exporters."""
    edges = [0.] if points[0][1] > .5 else []
    for (a,x),(b,y) in zip(points,points[1:]):
        if (x > .5) != (y > .5):
            t=a+(b-a)*(.5-x)/(y-x)
            if t < stop:
                edges.append(t)
    # A final off edge beyond the stop keeps the GATING list paired.
    if len(edges)%2:
        edges.append(stop*2)
    return edges


def describe(circuit, name):
    if circuit.control:
        raise SpiceError('PSIM 暂不支持闭环控制表导出；请使用 PLECS／Simulink，或另存并明确配置开环工况')
    if circuit.subsystems:
        raise SpiceError('PSIM 暂不支持折叠模块子系统；请先在编辑器展开模块再导出')
    unsupported = sorted({p.kind for p in circuit.parts if p.kind not in KINDS and p.kind not in ('V','I')})
    if unsupported:
        raise SpiceError('PSIM 暂不支持这些器件模型：'+', '.join(unsupported)+'；不会忽略或替换它们')
    signals={s.name:s for s in circuit.signals}
    if any(s.kind not in ('dc','sine','pwl') for s in signals.values()):
        raise SpiceError('PSIM 仅支持直流、正弦和已检查的分段线性驱动')
    parts=deepcopy(circuit.parts)
    for p in list(parts):
        if p.kind=='I':
            # PSIM current sources deliver current from port 2 into port 1.
            p.nodes.reverse()
        if p.kind=='S':
            series,shunt=_switch_resistors(p.params['ron'],p.params['roff'])
            old=p.nodes[1]
            internal='psim_'+p.name
            while any(internal in q.nodes for q in parts):
                internal+='x'
            p.nodes[1]=internal
            parts.extend([Part(p.name+'_ron','R',[internal,old],{'value':series},p.position,p.source),
                          Part(p.name+'_roff','R',[p.nodes[0],old],{'value':shunt},p.position,p.source)])
        if p.kind=='T' and p.params['ratio']<0:
            p.nodes[2:]=reversed(p.nodes[2:])
    drawing=circuit.drawing
    layout=plan(parts,drawing['elements'],drawing['wires'],circuit.pin_nodes,terminal,gate_side)
    nets={n:'CDN'+str(i) for i,n in enumerate(sorted({n for p in parts for n in p.nodes}),1)}
    commands=[]
    def add(kind,label,**params):
        commands.append([kind,label,params])
    def ports(points):
        return '{'+','.join(str(v) for xy in points for v in xy)+'}'
    def label(net,xy,direction=0):
        add('LABEL',nets[net],PORTS=ports([xy]),DIRECTION=direction)
    step=min(circuit.transient['step'],circuit.transient['max_step'])
    # Bound native output volume without rounding the requested solver step.
    print_step=max(1,math.floor(circuit.transient['step']/step))
    stop=circuit.transient['stop']
    add('SIMCONTROL','',POINT='{40,40}',TIMESTEP=step,TIMESTEPTYPE=0,TOTALTIME=stop,PRINTSTEP=print_step,
        RSWITCHON=SWITCH_ON,RSWITCHOFF=SWITCH_OFF)
    channels={}
    for p in parts:
        spot=layout.spots[p.name]
        xy=[tuple(a+b for a,b in zip(spot.pos,terminal(p,i,spot.direction))) for i in range(len(p.nodes))]
        # Source defaults point up, passives point left, transformer ports are
        # primary top/bottom then secondary top/bottom in the vendor model.
        angle=(ANGLES[spot.direction]+(90 if p.kind in ('V','I') else 0 if p.kind=='T' else 180))%360
        params={'DIRECTION':angle}
        kind=KINDS.get(p.kind)
        v=p.params
        if p.kind in ('R','L','C'):
            params[{'R':'Resistance','L':'Inductance','C':'Capacitance'}[p.kind]]=v['value']
            if p.kind!='R':
                params['Initial_Current' if p.kind=='L' else 'Init__Cap__Voltage']=v['initial']
        elif p.kind=='D':
            params.update(Diode_Voltage_Drop=v['vf'],Rd=v['ron'])
        elif p.kind=='T':
            params.update(Np__primary_=1,Ns__secondary_=abs(v['ratio']))
        elif p.kind in ('V','I'):
            s=signals[v['signal']]
            kind,source=_source(s,p.kind)
            params.update(source)
        if p.kind=='S':
            gate=tuple(a+b for a,b in zip(spot.pos,gate_side(p,spot.direction)))
            xy.append(gate)
            edges=_edges(signals[v['signal']].points,stop) or [stop*2,stop*3]
            params['Initial_Position']=0
            if edges:
                add('GATING','gate_'+p.name,PORTS=ports([gate]),DIRECTION=angle,Frequency=0,
                    No__of_Points=len(edges),Switching_Points=','.join(format(t,'.17g') for t in edges))
        params['PORTS']=ports(xy)
        add(kind,p.name,**params)
        for i,net in enumerate(p.nodes):
            label(net,xy[i],(angle+180*i)%360)
        if p.kind in ('VM','AM'):
            channels[p.name]=p.name
    # Probe the same finite-edge gate waveform used to obtain switch events.
    for index,o in enumerate(circuit.outputs):
        if o['kind']=='drive' and o['origin'] not in channels:
            x,y=layout.box[2]+260,120+index*100
            native='drive_'+str(index)
            kind,params=_source(signals[o['origin']],'V')
            add(kind,native,PORTS=ports([(x,y),(x,y+50)]),DIRECTION=0,**params)
            add('Ground',native+'_gnd',PORTS=ports([(x,y+50)]))
            add('VP',native+'_probe',PORTS=ports([(x,y)]))
            channels[o['origin']]=native+'_probe'
    ground=next((p for p in parts if '0' in p.nodes),None)
    if ground is None:
        raise SpiceError('PSIM 模型没有参考地')
    add('Ground','reference',PORTS='{40,90}')
    label('0',(40,90))
    return {'model':name,'commands':commands,'columns':circuit.columns,
            'channels':[channels[o['origin']] for o in circuit.outputs],
            'node_labels':nets,'step':step,'print_step':print_step,'output_step':circuit.transient['step']}


def _source(signal, prefix):
    v=signal.params
    if signal.kind=='dc':
        return prefix+'DC',{'Amplitude':v['dc']}
    if signal.kind=='sine':
        if v.get('damping',0):
            raise SpiceError('PSIM 暂不支持衰减正弦源；不会忽略 damping')
        # VSIN is zero before Tstart, whereas the shared model holds its
        # initial sine value until delay; an explicit math source preserves it.
        return prefix+'MATH',{'Expression':f"{v['offset']:.17g}+{v['amplitude']:.17g}*sin(2*3.141592653589793*{v['frequency']:.17g}*max(t-{v['delay']:.17g},0)+{v['phase']:.17g}*3.141592653589793/180)", 'Tstart':0}
    if signal.kind=='pwl':
        return prefix+'GNL',{'Frequency':0,'No__of_Points_n':len(signal.points),
            'Times_T1__Tn':','.join(format(t,'.17g') for t,_ in signal.points),
            ('Values_V1__Vn' if prefix=='V' else 'Values_I1__In'):','.join(format(v,'.17g') for _,v in signal.points)}
    raise SpiceError('PSIM 不支持该驱动波形：'+signal.kind)


def emit(circuit,name):
    data=describe(circuit,name)
    circuit.notes.append('PSIM：按源图位置投影器件，用 CDN 网络标签连接已检查的引脚，避免跨线误连；标签对应关系保存在生成脚本的数据中。')
    circuit.notes.append('PSIM：开环 R/L/C、线性化二极管、双向开关、理想多绕组变压器及电压／电流测量；闭环控制、折叠子系统、IGBT、晶闸管和 PMSM 暂不支持，会明确拒绝。')
    circuit.notes.append(f"PSIM：请求固定步长 {data['step']:.9g} s；求解器可能为开关边沿进一步缩小。CSV 按输出间隔 {data['output_step']:.9g} s 对原生采样线性插值，不保存全部内部小步。")
    return _SCRIPT.replace('__DATA__',repr(json.dumps(data,ensure_ascii=False)))


_SCRIPT = '''"""Generated PSIM model. Requires the official psimapipy package and PSIM 2026+."""
import argparse
from bisect import bisect_left
import csv
import json
import math
import os
from pathlib import Path
import sys

DATA = json.loads(__DATA__)

def measured_rows(columns):
    # The native solver can refine the requested step around gating events.
    # Read its buffers directly; never copy millions of internal samples.
    count=columns[0].Rows
    if not count:
        raise SystemExit('PSIM returned no measurements.')
    times=columns[0].Values
    spacing=DATA['output_step']
    first=max(1,math.ceil(times[0]/spacing-1e-8))
    last=math.floor(times[count-1]/spacing+1e-4)
    for sample in range(first,last+1):
        t=sample*spacing
        right=bisect_left(times,t,0,count)
        if right==0 or right==count:
            index=min(right,count-1)
            yield [t,*(c.Values[index] for c in columns[1:])]
        else:
            left=right-1
            weight=(t-times[left])/(times[right]-times[left])
            yield [t,*(c.Values[left]+weight*(c.Values[right]-c.Values[left]) for c in columns[1:])]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--psim-home', default=os.environ.get('PSIM_HOME',''))
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--run', action='store_true', help='also simulate and write the measured CSV')
    parser.add_argument('--overwrite', action='store_true', help='replace an existing generated PSIM model and results')
    args=parser.parse_args()
    out=args.out.resolve()
    model=out/(DATA['model']+'.psimsch')
    protected=[model]+([out/(DATA['model']+suffix) for suffix in ('.csv','.runtime.json')] if args.run else [])
    existing=next((path for path in protected if path.exists()),None)
    if existing and not args.overwrite:
        raise SystemExit('Output already exists: '+str(existing)+'. Use another --out directory or explicitly pass --overwrite.')
    out.mkdir(parents=True,exist_ok=True)
    temporary=out/'psim-temp'
    temporary.mkdir(exist_ok=True)
    os.environ['TEMP']=os.environ['TMP']=str(temporary)
    sys.dont_write_bytecode=True
    try:
        from psimapipy import PSIM
    except ImportError:
        if not args.psim_home:
            raise SystemExit('Install the official psimapipy package, or pass --psim-home to your PSIM installation.')
        sys.path.insert(0,str(Path(args.psim_home)/'Python/Source/psimapipy-sources'))
        from Psim_Class import PSIM
    runtime=PSIM(args.psim_home)
    if not runtime.IsValid():
        raise SystemExit('PSIM could not initialize; check its installation and license.')
    if runtime.get_psim_version()[0]<2026:
        raise SystemExit('This builder requires PSIM 2026 or later.')
    schematic=runtime.PsimFileNew()
    if not schematic:
        raise SystemExit('PSIM could not create the schematic.')
    for kind,name,params in DATA['commands']:
        # Wires can return -2. The vendor SDK documents -1 as failure.
        result=runtime.PsimCreateNewElement(schematic,kind,name,**params)
        if result is None or result==-1:
            raise SystemExit('PSIM could not create '+kind+' '+name)
    if not runtime.PsimFileSave(schematic,str(model)):
        raise SystemExit('PSIM could not save '+str(model))
    print(model)
    if args.run:
        result=runtime.PsimSimulate(str(model),str(out/(DATA['model']+'.smv')),Simview=-1,TimeStepType=0)
        if not result.Result:
            raise SystemExit(result.ErrorMessage)
        curves={c.Name:c for c in result.Graph}
        expected=['Time',*DATA['channels']]
        missing=[n for n in expected if n not in curves]
        if missing:
            raise SystemExit('PSIM omitted requested channels: '+', '.join(missing))
        columns=[curves[n] for n in expected]
        if len({c.Rows for c in columns})!=1:
            raise SystemExit('PSIM returned mismatched channel lengths.')
        path=out/(DATA['model']+'.csv')
        samples=0
        with path.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            writer.writerow(DATA['columns'])
            for row in measured_rows(columns):
                writer.writerow(row)
                samples+=1
        times=columns[0]
        summary={'psim_version':runtime.get_psim_version_name(),
                 'requested_step_s':DATA['step'],'output_step_s':DATA['output_step'],
                 'graph_samples':times.Rows,'csv_samples':samples,'save_every_n_steps':DATA['print_step'],
                 'first_time_s':times.Values[0],'last_time_s':times.Values[times.Rows-1],
                 'observed_solver_step_s':(times.Values[1]-times.Values[0])/DATA['print_step'] if times.Rows>1 else None}
        (out/(DATA['model']+'.runtime.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(path)

if __name__=='__main__':
    main()
'''
