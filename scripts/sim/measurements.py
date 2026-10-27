"""Scientific quantities: retain source spelling; unit case carries meaning."""
from __future__ import annotations
import re

_UNITS = ('minutes','minute','seconds','second','hours','hour','°C','℃','도','분','초','시간','min','sec','rpm','RPM','x g','×g',
          'kg','mg','µg','μg','ug','g','µL','μL','uL','µl','ul','mL','ml','L','l',
          'mM','µM','μM','uM','nM','M','mm','cm','µm','μm','um','nm','s','h','C','배','회','번','%')
_NUMBER=r'[+−-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d+)?'
_PATTERN=re.compile(r'(?<![0-9A-Za-z./^])('+_NUMBER+r')\s*(?:('+
                    '|'.join(re.escape(u) for u in sorted(_UNITS,key=len,reverse=True))+
                    r')(?![A-Za-z0-9_µμ]))?')
_ALIASES={'minutes':'min','minute':'min','seconds':'s','second':'s','hours':'h','hour':'h','℃':'°C','도':'°C','초':'s','sec':'s','분':'min','시간':'h',
          'μL':'µL','uL':'µL','µl':'µL','ul':'µL','ml':'mL',
          'μM':'µM','uM':'µM','μm':'µm','um':'µm','μg':'µg','ug':'µg','RPM':'rpm','l':'L'}


def source_values(text: str) -> list[tuple[str,str]]:
    """Verbatim value/unit spellings for synthetic input; never casefold SI units."""
    values=[]
    for m in _PATTERN.finditer(text):
        start=m.start()
        if start>1 and text[start-1] in '+−-' and text[start-2] in 'eE^/':
            continue
        if m[2] and re.search(r'\d\s+$',text[:start]):
            continue  # Ambiguous grouping/step labels: never invent a quantity.
        values.append((m[1],m[2] or ''))
    return values


def number_units(text: str) -> list[tuple[str,str]]:
    return [(n.replace(',','.').replace('−','-'),_ALIASES.get(u,u)) for n,u in source_values(text)]
