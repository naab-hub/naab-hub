#!/usr/bin/env python3
"""Refresh the NAAB user profile from public GitHub repository metadata."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
API = 'https://api.github.com'
COLORS = ('#21D4C2', '#318CF5', '#9F9AFB', '#F7B65E', '#7895B4')
BG, PANEL, BORDER = '#081629', '#0D2036', '#24435A'
FG, MUTED = '#EAF5FF', '#A4BCD0'
FONT = 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, Arial, sans-serif'
SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)


def fetch_json(path):
    request = Request(API + path, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'NAAB-public-profile',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def is_source_project(repo, login):
    return (repo['owner']['login'].casefold() == login.casefold()
            and repo['private'] is False and repo.get('visibility', 'public') == 'public'
            and repo['fork'] is False
            and repo['name'].casefold() not in {login.casefold(), '.github', '.github-private'})


def public_projects(login, fetch=fetch_json):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', login):
        raise ValueError('Invalid GitHub login')
    account = fetch(f'/users/{login}')
    if account['login'].casefold() != login.casefold() or account['type'] != 'User':
        raise ValueError('Expected the matching GitHub user account')
    projects = {}
    # Empty, partial, exact-page and multi-page responses are handled explicitly.
    for page in range(1, 1001):
        query = urlencode({'type': 'owner', 'per_page': 100, 'page': page,
                           'sort': 'full_name', 'direction': 'asc'})
        batch = fetch(f'/users/{login}/repos?{query}')
        if not isinstance(batch, list):
            raise ValueError('GitHub repository response is not a list')
        projects.update((repo['id'], repo) for repo in batch if is_source_project(repo, login))
        if len(batch) < 100:
            return list(projects.values())
    raise RuntimeError('Pagination limit reached; no incomplete snapshot will be published')


@dataclass(frozen=True)
class Snapshot:
    projects: int
    stars: int
    forks: int
    recent: int
    months: tuple
    older: int
    languages: tuple
    refreshed: str


def last_six_months(now):
    month_index = now.year * 12 + now.month - 1
    return [(i // 12, i % 12 + 1) for i in range(month_index - 5, month_index + 1)]


def push_dates(projects, now):
    dates = []
    for repo in projects:
        if repo.get('pushed_at'):
            pushed = datetime.fromisoformat(repo['pushed_at'].replace('Z', '+00:00'))
            if pushed <= now:
                dates.append(pushed)
    return dates


def language_distribution(projects):
    counts = Counter(repo.get('language') or 'Unclassified' for repo in projects)
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0].casefold()))
    return tuple(ordered[:4] + ([('Other', sum(count for _, count in ordered[4:]))]
                               if len(ordered) > 4 else []))


def summarize(projects, now):
    dates = push_dates(projects, now)
    month_counts = Counter((date.year, date.month) for date in dates)
    months = tuple((datetime(year, month, 1).strftime('%b'), month_counts[year, month])
                   for year, month in last_six_months(now))
    return Snapshot(len(projects), sum(repo['stargazers_count'] for repo in projects),
                    sum(repo['forks_count'] for repo in projects),
                    sum(now - date <= timedelta(days=30) for date in dates), months,
                    len(projects) - sum(count for _, count in months),
                    language_distribution(projects), now.strftime('%Y-%m-%d %H:%M UTC'))


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    columns: int
    activity: Box
    languages: Box


DESKTOP = Layout(1080, 390, 4, Box(24, 146, 510, 216), Box(546, 146, 510, 216))
MOBILE = Layout(540, 810, 2, Box(24, 238, 492, 250), Box(24, 500, 492, 250))


def element(svg, tag, attrs, text=None):
    child = ET.SubElement(svg, f'{{{SVG_NS}}}{tag}', {key: str(val) for key, val in attrs.items()})
    child.text = text


def rect(svg, box, fill, style):
    element(svg, 'rect', {'x': box.x, 'y': box.y, 'width': box.w, 'height': box.h,
                          'fill': fill, **style})


def label(svg, position, caption, style):
    x, y = position
    element(svg, 'text', {'x': x, 'y': y, 'font-family': FONT, 'fill': FG, **style}, str(caption))


def panel(svg, box, title, subtitle):
    rect(svg, box, PANEL, {'rx': 12, 'stroke': BORDER})
    label(svg, (box.x+20, box.y+30), title, {'font-size': 20, 'font-weight': 650})
    label(svg, (box.x+20, box.y+51), subtitle, {'font-size': 13, 'fill': MUTED})


def compact_number(count):
    if count < 10000:
        return f'{count:,}'
    return f'{count/1000:.1f}k' if count < 1000000 else f'{count/1000000:.1f}m'


def metric_cards(svg, layout, snapshot):
    metrics = (('Public projects', snapshot.projects), ('Total stars', snapshot.stars),
               ('Total forks', snapshot.forks), ('Pushed in 30d', snapshot.recent))
    width = (layout.width - 48 - 12*(layout.columns-1))/layout.columns
    for i, (caption, count) in enumerate(metrics):
        x = 24 + i % layout.columns*(width+12)
        y = 56 + i // layout.columns*91
        rect(svg, Box(x,y,width,78), PANEL, {'rx':10,'stroke':BORDER})
        label(svg,(x+15,y+24),caption,{'font-size':15,'fill':MUTED})
        label(svg,(x+15,y+60),compact_number(count),{'font-size':30,'font-weight':700})
        rect(svg, Box(x+width-22,y+17,7,7), COLORS[0], {'rx':3})


def activity_chart(svg, box, snapshot):
    panel(svg, box, 'Repository activity', 'Latest push month · past 6 months')
    top, bottom = box.y+78, box.y+box.h-48
    step = (box.w-40)/6
    peak = max(1, max(count for _, count in snapshot.months))
    for fraction in (0,.5,1):
        y = bottom - fraction*(bottom-top)
        element(svg,'line',{'x1':box.x+20,'y1':y,'x2':box.x+box.w-20,'y2':y,'stroke':BORDER})
    for i, (month, count) in enumerate(snapshot.months):
        x = box.x+20+(i+.5)*step
        bar_height = (bottom-top)*count/peak
        if count:
            rect(svg,Box(x-16,bottom-bar_height,32,bar_height),COLORS[0] if i==5 else COLORS[1],{'rx':3})
        label(svg,(x,bottom-bar_height-5),count,{'font-size':13,'text-anchor':'middle'})
        label(svg,(x,bottom+17),month,{'font-size':12,'fill':MUTED,'text-anchor':'middle'})
    label(svg,(box.x+20,box.y+box.h-10),f'Older / no recorded push: {snapshot.older} · Not commit counts',{'font-size':12,'fill':MUTED})


def language_chart(svg, box, snapshot):
    panel(svg,box,'Language distribution','Primary language · by repository count')
    if not snapshot.projects:
        label(svg,(box.x+box.w/2,box.y+115),'No public source projects yet',{'font-size':17,'fill':MUTED,'text-anchor':'middle'})
        return
    x = box.x+20
    for i, (language,count) in enumerate(snapshot.languages):
        span = (box.w-40)*count/snapshot.projects
        rect(svg,Box(x,box.y+68,span,10),COLORS[i],{})
        x += span
        y = box.y+98+i*20
        rect(svg,Box(box.x+20,y-11,11,11),COLORS[i],{'rx':3})
        short_name = language if len(language) <= 30 else language[:27]+'...'
        label(svg,(box.x+39,y),short_name,{'font-size':14})
        label(svg,(box.x+box.w-20,y),f'{count} · {count/snapshot.projects:.0%}',{'font-size':14,'fill':MUTED,'text-anchor':'end'})


def footer(svg, layout):
    caption = 'Public source repositories only · excludes forks and profile repository'
    label(svg,(24,layout.height-24),caption,{'font-size':11,'fill':MUTED})
    label(svg,(layout.width-24,layout.height-8),'Source: GitHub REST API',{'font-size':11,'fill':MUTED,'text-anchor':'end'})


def dashboard(snapshot, layout):
    svg = ET.Element(f'{{{SVG_NS}}}svg', {'width':str(layout.width),'height':str(layout.height),
        'viewBox':f'0 0 {layout.width} {layout.height}','role':'img','aria-labelledby':'title desc'})
    element(svg,'title',{'id':'title'},'NAAB public GitHub activity')
    element(svg,'desc',{'id':'desc'},f'{snapshot.projects} public projects, {snapshot.stars} stars, {snapshot.forks} forks. Refreshed {snapshot.refreshed}.')
    rect(svg,Box(.5,.5,layout.width-1,layout.height-1),BG,{'rx':16,'stroke':BORDER})
    label(svg,(24,36),'GitHub overview',{'font-size':24,'font-weight':700})
    label(svg,(layout.width-24,36),snapshot.refreshed,{'font-size':13,'fill':COLORS[0],'text-anchor':'end'})
    metric_cards(svg,layout,snapshot)
    activity_chart(svg,layout.activity,snapshot)
    language_chart(svg,layout.languages,snapshot)
    footer(svg,layout)
    return ET.tostring(svg,encoding='unicode')+'\n'


def write_cards(output, snapshot):
    cards = {'naab-activity.svg': dashboard(snapshot,DESKTOP),
             'naab-activity-mobile.svg': dashboard(snapshot,MOBILE)}
    output.mkdir(parents=True, exist_ok=True)
    for name, svg in cards.items():
        pending = output/(name+'.tmp')
        pending.write_text(svg,encoding='utf-8')
        pending.replace(output/name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', default=os.environ.get('GITHUB_REPOSITORY_OWNER', 'naab-hub'))
    args = parser.parse_args()
    snapshot = summarize(public_projects(args.owner), datetime.now(timezone.utc))
    write_cards(ROOT/'assets',snapshot)
    print(f'Refreshed {snapshot.projects} public source projects at {snapshot.refreshed}.')


if __name__ == '__main__':
    main()
