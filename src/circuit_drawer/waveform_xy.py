"""A time-ordered XY trajectory from paired rows of the existing waveform CSV."""
from bisect import bisect_left, bisect_right
import math

from .emit import _callout
from .geometry import Scale
from .profiles import get_profile
from .regions import Callout, LINE_HEAD, ink_tikz

# Keep first/last and both coordinates' extrema in each bucket. Boundary
# interpolation is separate, so short operating intervals cannot disappear.
POINT_BUDGET = 2400
HEIGHT_RATIO = .7
ARROW_LENGTH_CM = LINE_HEAD * 1.5  # a short tangent pointer; omit it on shorter segments
START_RADIUS_CM, END_RADIUS_CM = .035, .06


def validate(cfg, data):
    if 'xy' not in cfg:
        return
    xy = cfg['xy']
    if not isinstance(xy, dict) or set(xy)-{'x','y','arrows','color_by_mode'}:
        raise ValueError('waveform.xy 只能包含 x、y、arrows、color_by_mode。')
    for name in ('x','y'):
        if not isinstance(xy.get(name), str) or xy[name] not in data.columns or xy[name] == cfg['time_column']:
            raise ValueError(f'waveform.xy.{name} 必须是 CSV 中的数值通道。')
    for name in ('arrows','color_by_mode'):
        if name in xy and not isinstance(xy[name], bool):
            raise ValueError(f'waveform.xy.{name} 必须是 true 或 false。')


def paired_samples(data, cfg):
    """Select common row indices, never zip independently decimated channels."""
    left,right = cfg['window']
    x,y = (data.columns[cfg['xy'][name]] for name in ('x','y'))
    first,last = bisect_left(data.time,left),bisect_right(data.time,right)
    step = max(1,math.ceil((last-first)/max(1,POINT_BUDGET//6)))
    points = {}
    for a in range(first,last,step):
        b = min(last,a+step)
        indices = {a,b-1}
        for values in (x,y):
            indices.update((min(range(a,b),key=values.__getitem__),max(range(a,b),key=values.__getitem__)))
        points.update((data.time[i],[data.time[i],x[i],y[i]]) for i in indices)
    for t in cfg['boundaries']:
        i = bisect_left(data.time,t)
        if data.time[i] == t:
            points[t] = [t,x[i],y[i]]
        else:
            q = (t-data.time[i-1])/(data.time[i]-data.time[i-1])
            points[t] = [t,x[i-1]+q*(x[i]-x[i-1]),y[i-1]+q*(y[i]-y[i-1])]
    return [points[t] for t in sorted(points)]


def direction(points):
    """One tangent at half the travelled paper distance; constant states have none."""
    lengths = [math.dist(a,b) for a,b in zip(points,points[1:])]
    total = sum(lengths)
    if total < ARROW_LENGTH_CM:
        return None
    distance = total/2
    for a,b,length in zip(points,points[1:],lengths):
        if length and distance <= length:
            q = distance/length
            tip = [a[i]+q*(b[i]-a[i]) for i in (0,1)]
            tail = [tip[i]-(b[i]-a[i])/length*ARROW_LENGTH_CM for i in (0,1)]
            return [tail,tip]
        distance -= length
    return None


def preview(data, cfg, profile, names):
    from .waveforms import (COLORS, ROW_CM, CHART_LEFT_CM, CHART_RIGHT_CM, PT_CM, LEGEND_LINE_CM, LEGEND_GAP_CM,
                            axis, axis_title, column_defaults, legend, mode_tags)
    if 'xy' not in cfg:
        return None
    validate(cfg,data)
    prof = get_profile(profile); xy = cfg['xy']
    points = paired_samples(data,cfg)
    axes = [axis({},[[p[0],p[i]] for p in points],cfg['window']) for i in (1,2)]
    width = prof.width_cm-1.6; height = width*HEIGHT_RATIO
    def at(p):
        return [(p[i+1]-axes[i]['range'][0])/(axes[i]['range'][1]-axes[i]['range'][0])*span
                for i,span in enumerate((width,height))]
    tags = [tag if names.count(name)==1 else None for name,tag in zip(cfg['intervals'],mode_tags(cfg,names))]
    times = [p[0] for p in points]
    segments,keys = [],{}
    by_mode = xy.get('color_by_mode',True)
    for i,(left,right) in enumerate(zip(cfg['boundaries'],cfg['boundaries'][1:])):
        tag = tags[i] if by_mode else 0
        colour = '#666666' if tag is None else COLORS[(tag-1)%len(COLORS)] if tag else '#2457d6'
        label = f'Mode {tag}' if tag else 'Unbound' if tag is None else 'Trajectory'
        keys.setdefault(tag,{'label':label,'color':colour})
        segment = [at(p) for p in points[bisect_left(times,left):bisect_right(times,right)]]
        segments.append({'interval':i,'mode':tags[i],'color':colour,'points':segment,
                         'arrow':direction(segment) if xy.get('arrows',True) else None})
    labels,legend_height = legend(list(keys.values()),width,prof.font_pt,force=True)
    for label in labels:
        label['at'][1] += height-ROW_CM
    titles = []
    for name in ('x','y'):
        column = xy[name]
        channel = next((c for c in cfg['channels'] if c['column']==column),column_defaults(column))
        titles.append(axis_title(channel))
    return {'points':points,'axes':axes,'titles':titles,'segments':segments,'legend':labels,
            'ends':[at(points[0]),at(points[-1])],
            'paper':{'width_cm':width,'height_cm':height,'left_cm':CHART_LEFT_CM,'right_cm':CHART_RIGHT_CM,
                     'top_cm':legend_height+.2,'bottom_cm':1.15,
                     'font_pt':prof.font_pt,'line_pt':prof.line_pt,'pt_cm':PT_CM,
                     'legend_line_cm':LEGEND_LINE_CM,'legend_gap_cm':LEGEND_GAP_CM,
                     'arrow_head_cm':LINE_HEAD,'start_radius_cm':START_RADIUS_CM,'end_radius_cm':END_RADIUS_CM}}


def tex(data,cfg,profile,names):
    from .waveforms import LEGEND_LINE_CM,LEGEND_GAP_CM
    view = preview(data,cfg,profile,names)
    if view is None:
        raise ValueError('请先选择并应用 XY 图的横轴和纵轴通道。')
    p = view['paper']; width,height = p['width_cm'],p['height_cm']; prof=get_profile(profile)
    out=[r'\begin{tikzpicture}[x=1cm,y=1cm]',
         rf'\path[use as bounding box] (-{p["left_cm"]:g},-{p["bottom_cm"]:g}) rectangle ({width+p["right_cm"]:g},{height+p["top_cm"]:g});']
    for i,(axis,title) in enumerate(zip(view['axes'],view['titles'])):
        low,high=axis['range']; span=height if i else width
        for value,label in zip(axis['ticks'],axis['labels']):
            pos=(value-low)/(high-low)*span
            a,b=((0,pos),(width,pos)) if i else ((pos,0),(pos,height))
            out.append(rf'\draw[gray!25,line width=.25pt] ({a[0]:g},{a[1]:g}) -- ({b[0]:g},{b[1]:g});')
            anchor='east' if i else 'north'; x,y=(-.1,pos) if i else (pos,-.04)
            out.append(rf'\node[anchor={anchor}] at ({x:g},{y:g}) {{{label}}};')
        if i:
            out.append(rf'\node[rotate=90] at (-1.05,{height/2:g}) {{{title}}};')
        else:
            out.append(rf'\node[anchor=north] at ({width/2:g},-.48) {{{title}}};')
    out.append(rf'\draw[line width=.35pt] (0,{height:g}) -- (0,0) -- ({width:g},0);')
    for key in view['legend']:
        x,y=key['at'];ink=ink_tikz(key['color'])
        out.append(rf'\draw[draw={ink},line width={prof.line_pt:g}pt] ({x:g},{y:g}) -- ({x+LEGEND_LINE_CM:g},{y:g});')
        out.append(rf'\node[anchor=west,inner sep=1pt] at ({x+LEGEND_LINE_CM+LEGEND_GAP_CM:g},{y:g}) {{{key["text"]}}};')
    for segment in view['segments']:
        coords=' -- '.join(f'({x:.7f},{y:.7f})' for x,y in segment['points'])
        out.append(rf'\draw[draw={ink_tikz(segment["color"])},line width={prof.line_pt:g}pt,line join=round] {coords};')
        if segment['arrow']:
            a,b=segment['arrow']
            out.append(_callout(Callout(a,b,style='line',color=segment['color']),Scale(1,1),prof,False,weight=1).rstrip())
    start,end=view['ends']
    out.append(rf'\draw[black,fill=white,line width={prof.line_pt:g}pt] ({end[0]:g},{end[1]:g}) circle[radius={END_RADIUS_CM:g}];')
    out.append(rf'\fill[black] ({start[0]:g},{start[1]:g}) circle[radius={START_RADIUS_CM:g}];')
    out.append(rf'\node[anchor=north] at ({width/2:g},-.86) {{Start: filled; End: open}};')
    return '\n'.join(out+[r'\end{tikzpicture}'])
