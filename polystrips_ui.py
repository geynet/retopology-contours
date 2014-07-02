'''
Copyright (C) 2013 CG Cookie
http://cgcookie.com
hello@cgcookie.com

Created by Patrick Moore

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import bpy
import bmesh
import blf
import math
import sys
import copy
import time
from mathutils import Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_vector_3d, region_2d_to_location_3d
import contour_utilities, general_utilities
from contour_classes import ContourCutLine, ExistingVertList, CutLineManipulatorWidget, PolySkecthLine, ContourCutSeries, ContourStatePreserver
from polystrips import PolyStrips, GVert, GEdge
from mathutils.geometry import intersect_line_plane, intersect_point_line
from bpy.props import EnumProperty, StringProperty,BoolProperty, IntProperty, FloatVectorProperty, FloatProperty
from bpy.types import Operator, AddonPreferences

from polystrips import *

# Create a class that contains all location information for addons
AL = general_utilities.AddonLocator()

#a place to store stokes for later
global contour_cache 

#store any temporary triangulated objects
#store the bmesh to prevent recalcing bmesh
#each time :-)
global contour_mesh_cache

def draw_circle(context, c,n,r,col,step=10):
    x = Vector((0.42,-0.42,0.42)).cross(n).normalized() * r
    y = n.cross(x).normalized() * r
    d2r = math.pi/180
    p3d = [c+x*math.cos(i*d2r)+y*math.sin(i*d2r) for i in range(0,360+step,step)]
    contour_utilities.draw_polyline_from_3dpoints(context, p3d, col, 1, "GL_LINE_SMOOTH")

def polystrips_draw_callback(self, context):
    settings = context.user_preferences.addons[AL.FolderName].preferences
    cols = [(1,.5,.5,.8),(.5,1,.5,.8),(.5,.5,1,.8),(1,1,.5,.8)]
    
    for gedge in self.polystrips.gedges:
        p3d = []
        prev0,prev1 = None,None
        for i,gvert in enumerate(gedge.cache_igverts):
            if i%2 == 0:
                continue
            cur0,cur1 = gvert.position+gvert.tangent_y*gvert.radius,gvert.position-gvert.tangent_y*gvert.radius
            if prev0 and prev1:
                p3d += [prev0,cur0,cur1,prev1,cur1,cur0]
            else:
                p3d = [cur1,cur0]
            prev0,prev1 = cur0,cur1
        contour_utilities.draw_polyline_from_3dpoints(context, p3d, (1,.5,.5,.8), 1, "GL_LINE_SMOOTH")


class CGCOOKIE_OT_polystrips(bpy.types.Operator):
    bl_idname = "cgcookie.polystrips"
    bl_label  = "PolyStrips"
    
    @classmethod
    def poll(cls,context):
        if context.mode not in {'EDIT_MESH','OBJECT'}:
            return False
        
        if context.active_object:
            if context.mode == 'EDIT_MESH':
                if len(context.selected_objects) > 1:
                    return True
                else:
                    return False
            else:
                return context.object.type == 'MESH'
        else:
            return False
    
    def modal(self, context, event):
        context.area.tag_redraw()
        settings = context.user_preferences.addons[AL.FolderName].preferences
        
        # event details
        event_ctrl    = 'CTRL+'  if event.ctrl  else ''
        event_shift   = 'SHIFT+' if event.shift else ''
        event_alt     = 'ALT+'   if event.alt   else ''
        event_value   = event_ctrl + event_shift + event_alt + event.type
        event_press   = event_value if event.value == 'PRESS'   else None
        event_release = event_value if event.value == 'RELEASE' else None
        
        ####################################
        # general navigation event handling
        
        events_numpad = {
            'NUMPAD_1',       'NUMPAD_2',       'NUMPAD_3',
            'NUMPAD_4',       'NUMPAD_5',       'NUMPAD_6',
            'NUMPAD_7',       'NUMPAD_8',       'NUMPAD_9',
            'CTRL+NUMPAD_1',  'CTRL+NUMPAD_2',  'CTRL+NUMPAD_3',
            'CTRL+NUMPAD_4',  'CTRL+NUMPAD_5',  'CTRL+NUMPAD_6',
            'CTRL+NUMPAD_7',  'CTRL+NUMPAD_8',  'CTRL+NUMPAD_9',
            'SHIFT+NUMPAD_1', 'SHIFT+NUMPAD_2', 'SHIFT+NUMPAD_3',
            'SHIFT+NUMPAD_4', 'SHIFT+NUMPAD_5', 'SHIFT+NUMPAD_6',
            'SHIFT+NUMPAD_7', 'SHIFT+NUMPAD_8', 'SHIFT+NUMPAD_9',
            'NUMPAD_PLUS', 'NUMPAD_MINUS', # CTRL+NUMPAD_PLUS and CTRL+NUMPAD_MINUS are used later
            'NUMPAD_PERIOD',
        }
        handle_nav = False
        handle_nav |= event.type == 'MIDDLEMOUSE'
        handle_nav |= event.type == 'MOUSEMOVE' and self.is_navigating
        handle_nav |= event_value in events_numpad      # note: event_value handles ctrl,shift,alt
        handle_nav |= event.type.startswith('NDOF_')
        handle_nav |= event.type.startswith('TRACKPAD')
        handle_nav |= event_value in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} # note: event_value handles ctrl,shift,alt
        if handle_nav:
            self.is_navigating = (event.value == 'PRESS')
            self.post_update = True
            return {'PASS_THROUGH'}
        self.is_navigating = False
        
        if event_press in {'RET', 'NUMPAD_ENTER', 'ESC'}:
            contour_utilities.callback_cleanup(self, context)
            return {'CANCELLED'}
        
        if event_press == 'CTRL+NUMPAD_PLUS':
            self.polystrips.gverts[0].radius *= 1.1
            self.polystrips.gedges[0].recalc_igverts_approx()
            self.polystrips.gedges[0].snap_igverts_to_object(self.obj)
            return {'RUNNING_MODAL'}
        if event_press == 'CTRL+NUMPAD_MINUS':
            self.polystrips.gverts[0].radius /= 1.1
            self.polystrips.gedges[0].recalc_igverts_approx()
            self.polystrips.gedges[0].snap_igverts_to_object(self.obj)
            return {'RUNNING_MODAL'}
        if event_press == 'CTRL+SHIFT+NUMPAD_PLUS':
            self.polystrips.gverts[3].radius *= 1.1
            self.polystrips.gedges[0].recalc_igverts_approx()
            self.polystrips.gedges[0].snap_igverts_to_object(self.obj)
            return {'RUNNING_MODAL'}
        if event_press == 'CTRL+SHIFT+NUMPAD_MINUS':
            self.polystrips.gverts[3].radius /= 1.1
            self.polystrips.gedges[0].recalc_igverts_approx()
            self.polystrips.gedges[0].snap_igverts_to_object(self.obj)
            return {'RUNNING_MODAL'}
        
        return{'RUNNING_MODAL'}
    
    
    def invoke(self, context, event):
        #settings = context.user_preferences.addons[AL.FolderName].preferences
        #return {'CANCELLED'}
        #return {'RUNNING_MODAL'}
        
        self.is_navigating = False
        
        self.obj = context.object
        me = self.obj.to_mesh(scene=context.scene, apply_modifiers=True, settings='PREVIEW')
        me.update()
        self.bme = bmesh.new()
        self.bme.from_mesh(me)
        
        xform = bpy.data.objects['BezierCurve'].matrix_world
        data = bpy.data.objects['BezierCurve'].data
        p0 = xform * data.splines[0].bezier_points[0].co
        r0 = .2
        n0  = Vector((0,0,1))
        tx0 = Vector((1,0,0))
        ty0 = Vector((0,1,0))
        p1 = xform * data.splines[0].bezier_points[0].handle_right
        r1 = .1
        n1  = Vector((0,0,1))
        tx1 = Vector((1,0,0))
        ty1 = Vector((0,1,0))
        p2 = xform * data.splines[0].bezier_points[1].handle_left
        r2 = .1
        n2  = Vector((0,0,1))
        tx2 = Vector((1,0,0))
        ty2 = Vector((0,1,0))
        p3 = xform * data.splines[0].bezier_points[1].co
        r3 = .2
        n3  = Vector((0,0,1))
        tx3 = Vector((1,0,0))
        ty3 = Vector((0,1,0))
        
        gv0 = GVert(p0,r0,n0,tx0,ty0)
        gv1 = GVert(p1,r1,n1,tx1,ty1)
        gv2 = GVert(p2,r2,n2,tx2,ty2)
        gv3 = GVert(p3,r3,n3,tx3,ty3)
        
        ge0 = GEdge(gv0,gv1,gv2,gv3)
        ge0.recalc_igverts_approx()
        ge0.snap_igverts_to_object(self.obj)
        
        self.polystrips = PolyStrips(context, self.obj)
        self.polystrips.gverts = [gv0,gv1,gv2,gv3]
        self.polystrips.gedges = [ge0]
        
        # switch to modal
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            polystrips_draw_callback,
            (self, context),
            'WINDOW',
            'POST_PIXEL'
            )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
