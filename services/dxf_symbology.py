# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF to QGIS symbology - the full ACI palette and QML generation. Tuned against label lag on big drawings: scale-dependent visibility for texts and points, the labeling engine allowed to drop overlapping labels, and thresholds that tighten as the layer gets denser."""

# the 256 AutoCAD Color Index colors, as GDAL's DXF driver resolves them
ACI = {
    0: (0, 0, 0), 1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0),
    4: (0, 255, 255), 5: (0, 0, 255), 6: (255, 0, 255), 7: (0, 0, 0),
    8: (127, 127, 127), 9: (191, 191, 191),
    10: (255, 0, 0), 11: (255, 127, 127), 12: (165, 0, 0), 13: (165, 82, 82),
    14: (127, 0, 0), 15: (127, 63, 63), 16: (76, 0, 0), 17: (76, 38, 38),
    18: (38, 0, 0), 19: (38, 19, 19),
    20: (255, 63, 0), 21: (255, 159, 127), 22: (165, 41, 0), 23: (165, 103, 82),
    24: (127, 31, 0), 25: (127, 79, 63), 26: (76, 19, 0), 27: (76, 47, 38),
    28: (38, 9, 0), 29: (38, 23, 19),
    30: (255, 127, 0), 31: (255, 191, 127), 32: (165, 82, 0), 33: (165, 124, 82),
    34: (127, 63, 0), 35: (127, 95, 63), 36: (76, 38, 0), 37: (76, 57, 38),
    38: (38, 19, 0), 39: (38, 28, 19),
    40: (255, 191, 0), 41: (255, 223, 127), 42: (165, 124, 0), 43: (165, 145, 82),
    44: (127, 95, 0), 45: (127, 111, 63), 46: (76, 57, 0), 47: (76, 66, 38),
    48: (38, 28, 0), 49: (38, 33, 19),
    50: (255, 255, 0), 51: (255, 255, 127), 52: (165, 165, 0), 53: (165, 165, 82),
    54: (127, 127, 0), 55: (127, 127, 63), 56: (76, 76, 0), 57: (76, 76, 38),
    58: (38, 38, 0), 59: (38, 38, 19),
    60: (191, 255, 0), 61: (223, 255, 127), 62: (124, 165, 0), 63: (145, 165, 82),
    64: (95, 127, 0), 65: (111, 127, 63), 66: (57, 76, 0), 67: (66, 76, 38),
    68: (28, 38, 0), 69: (33, 38, 19),
    70: (127, 255, 0), 71: (191, 255, 127), 72: (82, 165, 0), 73: (124, 165, 82),
    74: (63, 127, 0), 75: (95, 127, 63), 76: (38, 76, 0), 77: (57, 76, 38),
    78: (19, 38, 0), 79: (28, 38, 19),
    80: (63, 255, 0), 81: (159, 255, 127), 82: (41, 165, 0), 83: (103, 165, 82),
    84: (31, 127, 0), 85: (79, 127, 63), 86: (19, 76, 0), 87: (47, 76, 38),
    88: (9, 38, 0), 89: (23, 38, 19),
    90: (0, 255, 0), 91: (127, 255, 127), 92: (0, 165, 0), 93: (82, 165, 82),
    94: (0, 127, 0), 95: (63, 127, 63), 96: (0, 76, 0), 97: (38, 76, 38),
    98: (0, 38, 0), 99: (19, 38, 19),
    100: (0, 255, 63), 101: (127, 255, 159), 102: (0, 165, 41), 103: (82, 165, 103),
    104: (0, 127, 31), 105: (63, 127, 79), 106: (0, 76, 19), 107: (38, 76, 47),
    108: (0, 38, 9), 109: (19, 38, 23),
    110: (0, 255, 127), 111: (127, 255, 191), 112: (0, 165, 82), 113: (82, 165, 124),
    114: (0, 127, 63), 115: (63, 127, 95), 116: (0, 76, 38), 117: (38, 76, 57),
    118: (0, 38, 19), 119: (19, 38, 28),
    120: (0, 255, 191), 121: (127, 255, 223), 122: (0, 165, 124), 123: (82, 165, 145),
    124: (0, 127, 95), 125: (63, 127, 111), 126: (0, 76, 57), 127: (38, 76, 66),
    128: (0, 38, 28), 129: (19, 38, 33),
    130: (0, 255, 255), 131: (127, 255, 255), 132: (0, 165, 165), 133: (82, 165, 165),
    134: (0, 127, 127), 135: (63, 127, 127), 136: (0, 76, 76), 137: (38, 76, 76),
    138: (0, 38, 38), 139: (19, 38, 38),
    140: (0, 191, 255), 141: (127, 223, 255), 142: (0, 124, 165), 143: (82, 145, 165),
    144: (0, 95, 127), 145: (63, 111, 127), 146: (0, 57, 76), 147: (38, 66, 76),
    148: (0, 28, 38), 149: (19, 33, 38),
    150: (0, 127, 255), 151: (127, 191, 255), 152: (0, 82, 165), 153: (82, 124, 165),
    154: (0, 63, 127), 155: (63, 95, 127), 156: (0, 38, 76), 157: (38, 57, 76),
    158: (0, 19, 38), 159: (19, 28, 38),
    160: (0, 63, 255), 161: (127, 159, 255), 162: (0, 41, 165), 163: (82, 103, 165),
    164: (0, 31, 127), 165: (63, 79, 127), 166: (0, 19, 76), 167: (38, 47, 76),
    168: (0, 9, 38), 169: (19, 23, 38),
    170: (0, 0, 255), 171: (127, 127, 255), 172: (0, 0, 165), 173: (82, 82, 165),
    174: (0, 0, 127), 175: (63, 63, 127), 176: (0, 0, 76), 177: (38, 38, 76),
    178: (0, 0, 38), 179: (19, 19, 38),
    180: (63, 0, 255), 181: (159, 127, 255), 182: (41, 0, 165), 183: (103, 82, 165),
    184: (31, 0, 127), 185: (79, 63, 127), 186: (19, 0, 76), 187: (47, 38, 76),
    188: (9, 0, 38), 189: (23, 19, 38),
    190: (127, 0, 255), 191: (191, 127, 255), 192: (82, 0, 165), 193: (124, 82, 165),
    194: (63, 0, 127), 195: (95, 63, 127), 196: (38, 0, 76), 197: (57, 38, 76),
    198: (19, 0, 38), 199: (28, 19, 38),
    200: (191, 0, 255), 201: (223, 127, 255), 202: (124, 0, 165), 203: (145, 82, 165),
    204: (95, 0, 127), 205: (111, 63, 127), 206: (57, 0, 76), 207: (66, 38, 76),
    208: (28, 0, 38), 209: (33, 19, 38),
    210: (255, 0, 255), 211: (255, 127, 255), 212: (165, 0, 165), 213: (165, 82, 165),
    214: (127, 0, 127), 215: (127, 63, 127), 216: (76, 0, 76), 217: (76, 38, 76),
    218: (38, 0, 38), 219: (38, 19, 38),
    220: (255, 0, 191), 221: (255, 127, 223), 222: (165, 0, 124), 223: (165, 82, 145),
    224: (127, 0, 95), 225: (127, 63, 111), 226: (76, 0, 57), 227: (76, 38, 66),
    228: (38, 0, 28), 229: (38, 19, 33),
    230: (255, 0, 127), 231: (255, 127, 191), 232: (165, 0, 82), 233: (165, 82, 124),
    234: (127, 0, 63), 235: (127, 63, 95), 236: (76, 0, 38), 237: (76, 38, 57),
    238: (38, 0, 19), 239: (38, 19, 28),
    240: (255, 0, 63), 241: (255, 127, 159), 242: (165, 0, 41), 243: (165, 82, 103),
    244: (127, 0, 31), 245: (127, 63, 79), 246: (76, 0, 19), 247: (76, 38, 47),
    248: (38, 0, 9), 249: (38, 19, 23),
    250: (84, 84, 84), 251: (118, 118, 118), 252: (152, 152, 152), 253: (186, 186, 186),
    254: (220, 220, 220), 255: (255, 255, 255),
}


# DXF lineweight in 1/100 mm -> mm
LW_MAP = {
    -3: 0.25, -2: 0.25, -1: 0.25, 0: 0.05, 5: 0.05, 9: 0.09, 13: 0.13,
    15: 0.15, 18: 0.18, 20: 0.20, 25: 0.25, 30: 0.30, 35: 0.35, 40: 0.40,
    50: 0.50, 53: 0.53, 60: 0.60, 70: 0.70, 80: 0.80, 90: 0.90, 100: 1.00,
    106: 1.06, 120: 1.20, 140: 1.40, 158: 1.58, 200: 2.00, 211: 2.11,
}


def aci_rgb(aci):
    """AutoCAD Color Index to (R, G, B), grey for anything off the table."""
    return ACI.get(abs(aci), (128, 128, 128))


def lw_mm(lw):
    """DXF lineweight code to mm, 0.25 for anything off the table."""
    return LW_MAP.get(lw, 0.25)


def lt_dash(ltype):
    """Linetype name to a QGIS custom dash string."""
    lt = str(ltype).upper()
    if "DASHED2" in lt or "HIDDEN2" in lt:
        return "2;1"
    if "DASHEDX2" in lt or "HIDDENX2" in lt:
        return "8;4"
    if "DASHED" in lt or "HIDDEN" in lt:
        return "4;2"
    # DASHDOT before DOT, "DASHDOT2" contains "DOT2"
    if "DASHDOT2" in lt:
        return "4;1;1;1"
    if "DASHDOT" in lt:
        return "8;2;2;2"
    if "DOT2" in lt:
        return "1;1"
    if "DOTTED" in lt or "DOT" in lt:
        return "1;3"
    if "CENTER2" in lt:
        return "16;2;4;2"
    if "CENTER" in lt:
        return "24;3;6;3"
    if "PHANTOM2" in lt:
        return "8;2;2;2;2;2"
    if "PHANTOM" in lt:
        return "16;2;2;2;2;2"
    return ""  # solid / Continuous


# QGIS spells symbol-layer data-defined keys in camelCase here, unlike the snake_case option names right next to them - outlineColor, not line_color
def _dd_props(keys, expression):
    """data_defined_properties block driving the given color keys off one expression, or the empty block when there is no expression."""
    if not expression or not keys:
        return ('<data_defined_properties><Option type="Map"><Option '
                'name="name" type="QString" value=""/><Option '
                'name="properties"/><Option name="type" type="QString" '
                'value="collection"/></Option></data_defined_properties>')
    escaped = expression.replace("&", "&amp;").replace('"', "&quot;")
    entries = "".join(
        f'<Option name="{key}" type="Map">'
        f'<Option name="active" type="bool" value="true"/>'
        f'<Option name="expression" type="QString" value="{escaped}"/>'
        f'<Option name="type" type="int" value="3"/>'
        f'</Option>'
        for key in keys)
    return ('<data_defined_properties><Option type="Map"><Option name="name" '
            'type="QString" value=""/><Option name="properties" type="Map">'
            f'{entries}</Option><Option name="type" type="QString" '
            'value="collection"/></Option></data_defined_properties>')


def make_qml(geom_type, r, g, b, width_mm=0.25, dash="", feature_count=0,
             color_expr=""):
    """QML for QGIS 3.x. geom_type is lines, polygons, points or texts, and feature_count tightens the scale thresholds - texts show below 1:5000, points below 1:10000, lines and polygons always, and layers over 10k features hide sooner. color_expr overrides the flat r/g/b per feature, for a CAD layer whose entities carry their own colors; the r/g/b still go in as the static value so a QGIS that ignores the expression, or a feature the expression cannot resolve, lands on the layer color rather than on nothing."""
    cs = f"{r},{g},{b},255"
    cf = f"{r},{g},{b},30"
    # the polygon fill stays flat: it is a 30-alpha wash and a color expression would come back fully opaque, filling CAD outlines in solid
    dd_line = _dd_props(("outlineColor",), color_expr)
    dd_fill = _dd_props(("outlineColor",), color_expr)
    dd_marker = _dd_props(("fillColor", "outlineColor"), color_expr)

    text_max_scale = 3000 if feature_count > 10000 else 5000
    point_max_scale = 5000 if feature_count > 10000 else 10000

    if geom_type == "lines":
        style = "customdash" if dash else "solid"
        dash_opts = (
            f'<Option name="customdash" type="QString" value="{dash}"/>\n'
            f'            <Option name="use_custom_dash" type="QString" value="1"/>\n'
            f'            <Option name="customdash_unit" type="QString" value="MM"/>'
            if dash else
            '<Option name="use_custom_dash" type="QString" value="0"/>'
        )
        symbol_xml = f'''<symbol name="0" type="line" alpha="1" clip_to_extent="1" force_rhr="0">
        <data_defined_properties><Option type="Map"><Option name="name" type="QString" value=""/><Option name="properties"/><Option name="type" type="QString" value="collection"/></Option></data_defined_properties>
        <layer pass="0" class="SimpleLine" enabled="1" locked="0">
          <Option type="Map">
            <Option name="line_color" type="QString" value="{cs}"/>
            <Option name="line_width" type="QString" value="{width_mm:.3f}"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="line_style" type="QString" value="{style}"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="capstyle" type="QString" value="round"/>
            {dash_opts}
          </Option>
          {dd_line}
        </layer>
      </symbol>'''

        return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28" styleCategories="Symbology">
  <renderer-v2 type="singleSymbol" enableorderby="0" symbollevels="0" forceraster="0" referencescale="-1">
    <symbols>
      {symbol_xml}
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
</qgis>'''

    elif geom_type == "polygons":
        symbol_xml = f'''<symbol name="0" type="fill" alpha="1" clip_to_extent="1" force_rhr="0">
        <data_defined_properties><Option type="Map"><Option name="name" type="QString" value=""/><Option name="properties"/><Option name="type" type="QString" value="collection"/></Option></data_defined_properties>
        <layer pass="0" class="SimpleFill" enabled="1" locked="0">
          <Option type="Map">
            <Option name="color" type="QString" value="{cf}"/>
            <Option name="outline_color" type="QString" value="{cs}"/>
            <Option name="outline_width" type="QString" value="{width_mm:.3f}"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
            <Option name="outline_style" type="QString" value="solid"/>
          </Option>
          {dd_fill}
        </layer>
      </symbol>'''

        return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28" styleCategories="Symbology">
  <renderer-v2 type="singleSymbol" enableorderby="0" symbollevels="0" forceraster="0" referencescale="-1">
    <symbols>
      {symbol_xml}
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
</qgis>'''

    elif geom_type == "points":
        return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28" styleCategories="Symbology" maxScale="1" minScale="{point_max_scale}">
  <renderer-v2 type="singleSymbol" enableorderby="0" symbollevels="0" forceraster="0" referencescale="-1">
    <symbols>
      <symbol name="0" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
        <data_defined_properties><Option type="Map"><Option name="name" type="QString" value=""/><Option name="properties"/><Option name="type" type="QString" value="collection"/></Option></data_defined_properties>
        <layer pass="0" class="SimpleMarker" enabled="1" locked="0">
          <Option type="Map">
            <Option name="color" type="QString" value="{cs}"/>
            <Option name="outline_color" type="QString" value="{cs}"/>
            <Option name="size" type="QString" value="1.5"/>
            <Option name="size_unit" type="QString" value="MM"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
          {dd_marker}
        </layer>
      </symbol>
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
  <scaleVisibility hasScaleVisibility="1" maxScale="1" minScale="{point_max_scale}"/>
</qgis>'''

    else:  # texts
        # the labeling settings below are tuned for speed - a cap on the label count, a skip for texts under a few pixels, no collision obstacles. without them displayAll renders every label and zooming out lags badly
        return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28" styleCategories="Symbology|Labeling" maxScale="1" minScale="{text_max_scale}">
  <renderer-v2 type="singleSymbol" enableorderby="0" symbollevels="0" forceraster="0" referencescale="-1">
    <symbols>
      <symbol name="0" type="marker" alpha="0" clip_to_extent="1" force_rhr="0">
        <data_defined_properties><Option type="Map"><Option name="name" type="QString" value=""/><Option name="properties"/><Option name="type" type="QString" value="collection"/></Option></data_defined_properties>
        <layer pass="0" class="SimpleMarker" enabled="1" locked="0">
          <Option type="Map">
            <Option name="color" type="QString" value="{cs}"/>
            <Option name="size" type="QString" value="0.1"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style textColor="{cs}" fontFamily="Arial" fontSize="7"
                  fontWeight="50" fontItalic="0" namedStyle="Regular"
                  fieldName="Text" isExpression="0"
                  textOpacity="1" blendMode="0" multilineAlign="3"
                  allowHtml="0" fontKerning="1"
                  fontSizeMapUnitScale="3x:0,0,0,0,0,0"
                  fontSizeUnit="MapUnit" fontStrikeout="0"
                  fontUnderline="0" fontLetterSpacing="0"
                  fontWordSpacing="0"/>
      <text-buffer bufferDraw="1" bufferSize="0.5" bufferSizeUnits="MM"
                   bufferOpacity="1" bufferColor="255,255,255,200"
                   bufferNoFill="0" bufferJoinStyle="128" bufferBlendMode="0"/>
      <background shapeDraw="0"/>
      <shadow shadowDraw="0"/>
      <placement placement="0" centroidInside="0" centroidWhole="0"
                 dist="0" offsetType="0" quadOffset="4"
                 xOffset="0" yOffset="0" offsetUnits="MM"
                 fitInPolygonOnly="0" geometryGeneratorEnabled="0"
                 geometryGenerator="" geometryGeneratorType="PointGeometry"
                 layerType="PointLayer" priority="5"
                 preserveRotation="1" rotationAngle="0"/>
      <rendering displayAll="0" fontMinPixelSize="3" fontMaxPixelSize="10000"
                 scaleVisibility="1" scaleMin="1" scaleMax="{text_max_scale}"
                 minFeatureSize="0"
                 limitNumLabels="1" maxNumLabels="2000"
                 obstacle="0" obstacleFactor="1" obstacleType="0"
                 zIndex="0" drawLabels="1" upsidedownLabels="0"/>
      <dd_properties>
        <Option type="Map">
          <Option name="name" type="QString" value=""/>
          <Option name="properties" type="Map">
            <Option name="Size" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="if(&quot;text_height&quot; IS NOT NULL AND &quot;text_height&quot; &gt; 0, &quot;text_height&quot;, 2)"/>
              <Option name="type" type="int" value="3"/>
            </Option>
            <Option name="LabelRotation" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="if(&quot;rotation&quot; IS NOT NULL, -&quot;rotation&quot;, 0)"/>
              <Option name="type" type="int" value="3"/>
            </Option>
          </Option>
          <Option name="type" type="QString" value="collection"/>
        </Option>
      </dd_properties>
    </settings>
  </labeling>
  <scaleVisibility hasScaleVisibility="1" maxScale="1" minScale="{text_max_scale}"/>
</qgis>'''
