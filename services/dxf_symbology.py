# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF to QGIS symbology - the full ACI palette and QML generation. Tuned against label lag on big drawings: scale-dependent visibility for texts and points, the labeling engine allowed to drop overlapping labels, and thresholds that tighten as the layer gets denser."""

# the 256 AutoCAD Color Index colors
ACI = {
    0: (0, 0, 0), 1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0),
    4: (0, 255, 255), 5: (0, 0, 255), 6: (255, 0, 255), 7: (0, 0, 0),
    8: (65, 65, 65), 9: (128, 128, 128),
    10: (255, 0, 0), 11: (255, 170, 170), 12: (189, 0, 0), 13: (189, 126, 126),
    14: (129, 0, 0), 15: (129, 86, 86), 16: (104, 0, 0), 17: (104, 69, 69),
    18: (79, 0, 0), 19: (79, 53, 53),
    20: (255, 63, 0), 21: (255, 191, 170), 22: (189, 46, 0), 23: (189, 141, 126),
    24: (129, 31, 0), 25: (129, 96, 86), 26: (104, 25, 0), 27: (104, 78, 69),
    28: (79, 19, 0), 29: (79, 59, 53),
    30: (255, 127, 0), 31: (255, 212, 170), 32: (189, 94, 0), 33: (189, 157, 126),
    34: (129, 64, 0), 35: (129, 107, 86), 36: (104, 52, 0), 37: (104, 86, 69),
    38: (79, 39, 0), 39: (79, 66, 53),
    40: (255, 191, 0), 41: (255, 234, 170), 42: (189, 141, 0), 43: (189, 173, 126),
    44: (129, 96, 0), 45: (129, 118, 86), 46: (104, 78, 0), 47: (104, 95, 69),
    48: (79, 59, 0), 49: (79, 73, 53),
    50: (255, 255, 0), 51: (255, 255, 170), 52: (189, 189, 0), 53: (189, 189, 126),
    54: (129, 129, 0), 55: (129, 129, 86), 56: (104, 104, 0), 57: (104, 104, 69),
    58: (79, 79, 0), 59: (79, 79, 53),
    60: (191, 255, 0), 61: (234, 255, 170), 62: (141, 189, 0), 63: (173, 189, 126),
    64: (96, 129, 0), 65: (118, 129, 86), 66: (78, 104, 0), 67: (95, 104, 69),
    68: (59, 79, 0), 69: (73, 79, 53),
    70: (127, 255, 0), 71: (212, 255, 170), 72: (94, 189, 0), 73: (157, 189, 126),
    74: (64, 129, 0), 75: (107, 129, 86), 76: (52, 104, 0), 77: (86, 104, 69),
    78: (39, 79, 0), 79: (66, 79, 53),
    80: (63, 255, 0), 81: (191, 255, 170), 82: (46, 189, 0), 83: (141, 189, 126),
    84: (31, 129, 0), 85: (96, 129, 86), 86: (25, 104, 0), 87: (78, 104, 69),
    88: (19, 79, 0), 89: (59, 79, 53),
    90: (0, 255, 0), 91: (170, 255, 170), 92: (0, 189, 0), 93: (126, 189, 126),
    94: (0, 129, 0), 95: (86, 129, 86), 96: (0, 104, 0), 97: (69, 104, 69),
    98: (0, 79, 0), 99: (53, 79, 53),
    100: (0, 255, 63), 101: (170, 255, 191), 102: (0, 189, 46), 103: (126, 189, 141),
    104: (0, 129, 31), 105: (86, 129, 96), 106: (0, 104, 25), 107: (69, 104, 78),
    108: (0, 79, 19), 109: (53, 79, 59),
    110: (0, 255, 127), 111: (170, 255, 212), 112: (0, 189, 94), 113: (126, 189, 157),
    114: (0, 129, 64), 115: (86, 129, 107), 116: (0, 104, 52), 117: (69, 104, 86),
    118: (0, 79, 39), 119: (53, 79, 66),
    120: (0, 255, 191), 121: (170, 255, 234), 122: (0, 189, 141), 123: (126, 189, 173),
    124: (0, 129, 96), 125: (86, 129, 118), 126: (0, 104, 78), 127: (69, 104, 95),
    128: (0, 79, 59), 129: (53, 79, 73),
    130: (0, 255, 255), 131: (170, 255, 255), 132: (0, 189, 189), 133: (126, 189, 189),
    134: (0, 129, 129), 135: (86, 129, 129), 136: (0, 104, 104), 137: (69, 104, 104),
    138: (0, 79, 79), 139: (53, 79, 79),
    140: (0, 191, 255), 141: (170, 234, 255), 142: (0, 141, 189), 143: (126, 173, 189),
    144: (0, 96, 129), 145: (86, 118, 129), 146: (0, 78, 104), 147: (69, 95, 104),
    148: (0, 59, 79), 149: (53, 73, 79),
    150: (0, 127, 255), 151: (170, 212, 255), 152: (0, 94, 189), 153: (126, 157, 189),
    154: (0, 64, 129), 155: (86, 107, 129), 156: (0, 52, 104), 157: (69, 86, 104),
    158: (0, 39, 79), 159: (53, 66, 79),
    160: (0, 63, 255), 161: (170, 191, 255), 162: (0, 46, 189), 163: (126, 141, 189),
    164: (0, 31, 129), 165: (86, 96, 129), 166: (0, 25, 104), 167: (69, 78, 104),
    168: (0, 19, 79), 169: (53, 59, 79),
    170: (0, 0, 255), 171: (170, 170, 255), 172: (0, 0, 189), 173: (126, 126, 189),
    174: (0, 0, 129), 175: (86, 86, 129), 176: (0, 0, 104), 177: (69, 69, 104),
    178: (0, 0, 79), 179: (53, 53, 79),
    180: (63, 0, 255), 181: (191, 170, 255), 182: (46, 0, 189), 183: (141, 126, 189),
    184: (31, 0, 129), 185: (96, 86, 129), 186: (25, 0, 104), 187: (78, 69, 104),
    188: (19, 0, 79), 189: (59, 53, 79),
    190: (127, 0, 255), 191: (212, 170, 255), 192: (94, 0, 189), 193: (157, 126, 189),
    194: (64, 0, 129), 195: (107, 86, 129), 196: (52, 0, 104), 197: (86, 69, 104),
    198: (39, 0, 79), 199: (66, 53, 79),
    200: (191, 0, 255), 201: (234, 170, 255), 202: (141, 0, 189), 203: (173, 126, 189),
    204: (96, 0, 129), 205: (118, 86, 129), 206: (78, 0, 104), 207: (95, 69, 104),
    208: (59, 0, 79), 209: (73, 53, 79),
    210: (255, 0, 255), 211: (255, 170, 255), 212: (189, 0, 189), 213: (189, 126, 189),
    214: (129, 0, 129), 215: (129, 86, 129), 216: (104, 0, 104), 217: (104, 69, 104),
    218: (79, 0, 79), 219: (79, 53, 79),
    220: (255, 0, 191), 221: (255, 170, 234), 222: (189, 0, 141), 223: (189, 126, 173),
    224: (129, 0, 96), 225: (129, 86, 118), 226: (104, 0, 78), 227: (104, 69, 95),
    228: (79, 0, 59), 229: (79, 53, 73),
    230: (255, 0, 127), 231: (255, 170, 212), 232: (189, 0, 94), 233: (189, 126, 157),
    234: (129, 0, 64), 235: (129, 86, 107), 236: (104, 0, 52), 237: (104, 69, 86),
    238: (79, 0, 39), 239: (79, 53, 66),
    240: (255, 0, 63), 241: (255, 170, 191), 242: (189, 0, 46), 243: (189, 126, 141),
    244: (129, 0, 31), 245: (129, 86, 96), 246: (104, 0, 25), 247: (104, 69, 78),
    248: (79, 0, 19), 249: (79, 53, 59),
    250: (0, 0, 0), 251: (51, 51, 51), 252: (102, 102, 102), 253: (153, 153, 153),
    254: (204, 204, 204), 255: (255, 255, 255),
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


def make_qml(geom_type, r, g, b, width_mm=0.25, dash="", feature_count=0):
    """QML for QGIS 3.x. geom_type is lines, polygons, points or texts, and feature_count tightens the scale thresholds - texts show below 1:5000, points below 1:10000, lines and polygons always, and layers over 10k features hide sooner."""
    cs = f"{r},{g},{b},255"
    cf = f"{r},{g},{b},30"

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
