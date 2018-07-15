#
# Part of p5: A Python package based on Processing
# Copyright (C) 2017-2018 Abhik Pal
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
"""Shape class for p5.
"""

import builtins
import contextlib
import functools
import math

import numpy as np
from vispy import geometry

from .. import sketch
from .color import Color

__all__ = ['PShape']

def _ensure_editable(func):
    """A decorater that ensures that a shape is in 'edit' mode.

    """
    @functools.wraps(func)
    def editable_method(instance, *args, **kwargs):
        if not instance._in_edit_mode:
            raise ValueError('{} only works in edit mode'.format(func.__name__))
        return func(instance, *args, **kwargs)
    return editable_method


class PShape:
    """Custom shape class for p5.

    :param vertices: List of (polygonal) vertices for the shape.

    :type vertices: list | np.ndarray

    :param fill_color: Fill color of the shape (default: 'auto' i.e.,
        the current renderer fill)

    :type fill_color: 'auto' | None | tuple | p5.Color

    :param stroke_color: Stroke color of the shape (default: 'auto'
        i.e., the current renderer stroke color)

    :type stroke_color: 'auto' | None | tuple | p5.color

    :param visible: toggles shape visibility (default: False)

    :type visible: bool


    :param attribs: space-separated list of attributes that control
        shape drawing. Each attribute should be one of {'point',
        'path', 'open', 'closed'}. (default: 'closed')

    :type attribs: str

    :param children: List of sub-shapes for the current shape
        (default: [])

    :type children: list

    """
    def __init__(self, vertices=[], fill_color='auto',
                 stroke_color='auto', visible=False, attribs='closed',
                 children=[]):
        # basic properties of the shape
        self._vertices = np.array([])
        self._edges = None
        self._outline = None
        self._outline_vertices = None

        self.attribs = set(attribs.lower().split())
        self._fill = None
        self._stroke = None

        self._matrix = np.identity(4)
        self._transform_matrix = np.identity(4)
        self._transformed_draw_vertices = None

        # a flag to check if the shape is being edited right now.
        self._in_edit_mode = False
        self._vertex_cache = None

        # The triangulation used to render the shapes.
        self._tri = None
        self._tri_required = not ('point' in self.attribs) and \
                             not ('path' in self.attribs)
        self._tri_vertices = None
        self._tri_edges = None
        self._tri_faces = None

        if len(vertices) > 0:
            self.vertices = vertices

        # TODO: support different vertex types
        self._vertex_types = ['P'] * len(vertices)

        self.fill = fill_color
        self.stroke = stroke_color

        # TODO: support adding children nodes.
        self.children = children

        self.visible = visible

    def _set_color(self, name, value=None):
        color = None
        if value is None or value is 'auto':
            color = None
        elif isinstance(value, Color):
            color = value
        else:
            color = Color(*value)

        if value == 'auto':
            if name == 'stroke' and sketch.renderer.stroke_enabled:
                color = Color(*sketch.renderer.stroke_color,
                              color_mode='RGBA', normed=True)
            if name == 'fill' and sketch.renderer.fill_enabled:
                color = Color(*sketch.renderer.fill_color,
                              color_mode='RGBA', normed=True)

        if name == 'stroke':
            self._stroke = color
        elif name == 'fill':
            self._fill = color

    @property
    def fill(self):
        if isinstance(self._fill, Color):
            return self._fill
        return self._fill

    @fill.setter
    def fill(self, new_color):
        self._set_color('fill', new_color)

    @property
    def stroke(self):
        return self._stroke

    @stroke.setter
    def stroke(self, new_color):
       self._set_color('stroke', new_color)

    @property
    def kind(self):
        if 'point' in self.attribs:
            return 'point'
        elif 'path' in self.attribs:
            return 'path'
        else:
            return 'poly'

    def _sanitize_vertex_list(self, vertices, tdim=2, sdim=3):
        """Convert all vertices to the given dimensions.

        :param vertices: List of vertices
        :type vertices: list

        :param tdim: Target dimension for sanitization (default: 3)
        :type tdim: int

        :param sdim: Source dimension for the points (default: 2).
            Whenever sdim > tdim, the last (sdim - tdim) components will
            be discarded.
        :type sdim: int

        :raises ValueError: when the point dimension is between sdim and tdim

        :returns: A sanitized array of vertices.
        :type: np.ndarray

        """
        sanitized = []

        for v in vertices:
            if (len(v) > max(tdim, sdim)) or (len(v) < min(tdim, sdim)):
                raise ValueError("unexpected vertex dimension")

            if tdim > sdim:
                sanitized.append(list(v) + [0] * (tdim - sdim))
            elif tdim < sdim:
                sanitized.append(list(v)[:tdim])
            else:
                sanitized.append(list(v))

        return np.array(sanitized)

    @property
    def vertices(self):
        return self._vertices

    @vertices.setter
    def vertices(self, new_vertices):
        n = len(new_vertices)
        self._vertices = self._sanitize_vertex_list(new_vertices)
        self._outline_vertices = np.hstack([self._vertices, np.zeros((n, 1))])
        self._tri_vertices = None
        self._tri_edges = None
        self._tri_faces = None

    def _compute_poly_edges(self):
        n, _ = self._vertices.shape
        return np.vstack([np.arange(n), (np.arange(n) + 1) % n]).transpose()

    def _compute_outline_edges(self):
        n, _ = self._vertices.shape
        return np.vstack([np.arange(n - 1),
                          (np.arange(n - 1) + 1) % n]).transpose()

    @property
    def edges(self):
        if 'point' in self.attribs:
            return np.array([])

        if self._edges is None:
            n, _ = self._vertices.shape

            if 'point' in self.attribs:
                self._edges = np.array([])
            elif 'path' in self.attribs:
                self._edges = self._compute_outline_edges()
            else:
                self._edges = self._compute_poly_edges()

            if 'open' in self.attribs:
                self._outline = self._compute_outline_edges()
            else:
                self._outline = self._edges

        return self._edges

    def _retriangulate(self):
        """Triangulate the shape
        """
        self._tri = geometry.Triangulation(self.vertices, self.edges)
        self._tri.triangulate()

        if isinstance(self._tri.edges, np.ndarray):
            self._tri_edges = self._tri.edges
        else:
            self._tri_edges = np.array([])

        self._tri_faces = self._tri.tris
        self._tri_vertices = self._tri.pts

        if isinstance(self._tri.edges, np.ndarray):
            self._tri_edges = self._tri.edges
        else:
            self._tri_edges = np.array([])
            
        if isinstance(self._tri.tris, np.ndarray):
            self._tri_faces = self._tri.tris
        else:
            self._tri_faces = np.array([])

        self._tri_vertices = self._tri.pts

    @property
    def _draw_outline_vertices(self):
        if 'open' in self.attribs:
            return self._draw_vertices
        return self.vertices

    @property
    def _draw_outline_edges(self):
        if 'open' in self.attribs:
            return self._outline
        return self._edges

    @property
    def _draw_vertices(self):
        if self._tri_required and (self._tri_vertices is None):
            self._retriangulate()

        if self._tri_required:
            return self._tri_vertices
        return self._vertices

    @property
    def _draw_edges(self):
        if self._tri_required:
            if self._tri_edges is None:
                self._retriangulate()
            return self._tri_edges
        return self.edges

    @property
    def _draw_faces(self):
        if self._tri_required:
            if self._tri_faces is None:
                self._retriangulate()
            return self._tri_faces

        return np.array([])

    @contextlib.contextmanager
    def edit(self, reset=True):
        """Put the shape in edit mode.

        :param reset: Toggles whether the shape should be "reset"
            during editing. When set to `True` all existing shape
            vertices are cleared. When set to `False` the new vertices
            are appended at the end of the existing vertex list.
            (default: True)
        :type reset: bool

        :raises ValueError: if the shape is already being edited.

        """
        if self._in_edit_mode:
            raise ValueError("Shape is being edited already")

        self._in_edit_mode = True
        if reset:
            self._vertices = np.array([])
        self._vertex_cache = []
        yield
        self.vertices = self._vertex_cache
        self._in_edit_mode = False
        self._edges = None

    @_ensure_editable
    def add_vertex(self, vertex):
        """Add a vertex to the current shape

        :param vertex: The (next) vertex to add to the current shape.
        :type vertex: tuple | list | p5.Vector | np.ndarray

        :raises ValueError:  when the vertex is of the wrong dimension
        """
        self._vertex_cache.append(vertex)

    def update_vertex(self, idx, vertex):
        """Edit an indicidual vertex.

        :param idx: index of the vertex to be edited
        :type idx: int

        :param vertex: The (next) vertex to add to the current shape.
        :type vertex: tuple | list | p5.Vector | np.ndarray

        :raises ValueError:  when the vertex is of the wrong dimension
        """
        if len(vertex) != 2:
            raise ValueError("Wrong vertex dimension")
        self._vertices[idx] =  np.array(vertex)
        self._tri_vertices = None
        self._tri_edges = None
        self._tri_faces = None
        self._edges = None